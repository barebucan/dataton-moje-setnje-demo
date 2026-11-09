"""Two independent async loops and an audio/control transport for Lagano."""
import asyncio
import base64
import hashlib
import io
import json
import math
import re
import secrets
import time
import unicodedata
import wave
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response

from backend.voice_provider import OpenAIProvider, VoiceUnavailable, croatian_latin_text
from backend.voice_context import location_brief

BASE = Path(__file__).resolve().parents[1]
DEMO_CATALOG = {
    'green-horseshoe': {
        'name': 'Zelena potkova · HNK do Umjetničkog paviljona',
        'summary': 'Kazalište, arhitektura, znanost i umjetnost kroz osam provjerenih priča.',
        'anchors': ('culture-86', 'osm-node-1896949794', 'osm-way-392439336'),
        'places': ('culture-86', 'culture-195', 'osm-way-581658851', 'culture-89',
                   'osm-node-1896949794', 'osm-node-1509283469',
                   'osm-node-2884714406', 'osm-way-392439336'),
    },
    'theatres-tesla': {
        'name': 'Kazališta, arhitektura i Tesla',
        'summary': 'Kraći krug kroz četiri priče o zagrebačkoj moderni, sceni i izumima.',
        'anchors': ('culture-86', 'osm-way-581658851', 'culture-89',
                    'culture-195', 'culture-86'),
        'places': ('culture-86', 'culture-195', 'osm-way-581658851', 'culture-89'),
    },
}
DEMO_IDS = list(DEMO_CATALOG['green-horseshoe']['anchors'])

# Named public spaces/streets nearest to the preloaded demonstration route.
# Several walking edges in OSM are unnamed footways, so GraphHopper alone cannot
# label them. These local anchors keep the street context available offline.
STREET_ANCHORS = [
    ([15.9702, 45.8099], 'Trg Republike Hrvatske'),
    ([15.9710, 45.8102], 'Masarykova ulica'),
    ([15.9722, 45.8106], 'Masarykova ulica'),
    ([15.9744, 45.8112], 'Ulica Nikole Tesle'),
    ([15.9765, 45.8113], 'Ulica Nikole Tesle'),
    ([15.9781, 45.8112], 'Trg Nikole Zrinskog'),
    ([15.9781, 45.8104], 'Trg Nikole Zrinskog'),
    ([15.9779, 45.8093], 'Trg Josipa Jurja Strossmayera'),
    ([15.9780, 45.8084], 'Trg Josipa Jurja Strossmayera'),
    ([15.9781, 45.8076], 'Trg kralja Tomislava'),
    ([15.9786, 45.8072], 'Trg kralja Tomislava'),
]


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.lower())
                   if unicodedata.category(c) != 'Mn')


def is_transcription_artifact(text):
    normalized = re.sub(r'[^a-z0-9]+', ' ', normalize(text)).strip()
    return (normalized == 'govor je na hrvatskom jeziku zapisi ga hrvatskom latinicom s dijakritickim znakovima ne prevodi govor i ne dodaj rijeci'
            or normalized == 'hrvatski pjesacki vodic zagrebom hnk zrinjevac umjetnicki paviljon'
            or (normalized.startswith('hrvatski pjesacki vodic zagrebom')
                and {'hnk', 'zrinjevac', 'paviljon'} <= set(normalized.split())
                and len(normalized.split()) <= 12))


def distance(a, b):
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat, dlon = lat2-lat1, math.radians(b[0]-a[0])
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 6371000*2*math.asin(min(1, math.sqrt(h)))


def street_for(point):
    return min(STREET_ANCHORS, key=lambda item: distance(point, item[0]))[1]


def realtime_instructions(places, street='Trg Republike Hrvatske', interrupted=None, position=None):
    stories = '\n'.join(
        f"- {place['name']}: {place.get('look_for', '')}"
        for place in places
    )
    remaining = (interrupted or {}).get('remaining_text', '')
    interrupted_text = (f"Prekinuta tema koju možeš nastaviti samo ako korisnik pristane: "
                        f"{interrupted['name']}. Još neizgovoreni dio te teme je: "
                        f"{remaining or 'nastavak iz razgovora'}." if interrupted else
                        'Trenutačno nema prekinute teme za nastavak.')
    return (
        'Ti si smiren, topao hrvatski pješački vodič Zagrebom. Govori isključivo hrvatski, '
        'prirodno, malo brže od uobičajenog i bez vikanja. Ovo je jedna neprekinuta '
        'konverzacija: prije svakog odgovora provjeri što si već izgovorio i nikad ne ponavljaj '
        'istu činjenicu ili uvod, osim ako korisnik izričito traži ponavljanje. Ne govori o '
        'izvorima, pretraživanju ni nedostatku podataka. Ne izmišljaj činjenice. Rečenice mogu '
        'biti povezane. Govori u kratkim cjelinama od 4 do 5 kraćih rečenica, najviše 75 riječi. '
        'Kada položaj označi da smo napustili landmark, nemoj dodavati nove detalje o njemu. '
        'Jednom kratkom prijelaznom rečenicom prirodno prijeđi na sljedeće blisko mjesto. Automatski prijelaz uz '
        'rutu nije korisnikov prekid: tada nikad ne pitaj želi li povratak na staru temu. '
        'Nastavak od prve neizgovorene činjenice ponudi samo kada te korisnik stvarno prekine. '
        'Ponudu za istu prekinutu temu izgovori samo jednom; nakon korisnikova odgovora smatraj '
        'je riješenom i nemoj je kasnije ponovno nuditi. Ako korisnik odbije nastavak, ne govori '
        'ni potvrdu ni oproštaj: samo šuti i čekaj sljedeći korisnikov potez. '
        'Za pitanja koja traže novu provjeru postoji zaseban istraživački loop s pristupom webu. '
        'Rezultate dobivaš u istom razgovoru; nikad ne tvrdi da nemaš pristup internetu. '
        'Najnoviji dostavljeni položaj opisuje korisnika u ovoj šetnji, a ne tvoju fizičku lokaciju. '
        'Kad korisnik pita gdje je, odgovori iz tog položaja i najbliže znamenitosti. '
        'Lokacijska ažuriranja su tihi sistemski kontekst, nikada korisnikova poruka „nastavi“. '
        'Zbog njih ne prekidaj započeti odgovor, ne potvrđuj ih naglas i ne započinji novi govor. '
        'Odgovori na ono što korisnik pita. Pitanje o godini ili povijesti nije zahtjev za navigaciju. '
        'Ne nuditi smjerove ni ulazak u zgradu osim ako korisnik to izričito zatraži. '
        'Ne izgovaraj „samo trenutak“, „samo sekundu“, „da provjerim“ ni druge najave provjere. '
        f'{interrupted_text}\n'
        + location_brief(position, street) + '\n' +
        'Kratak popis mjesta na ruti; činjenice o pojedinom mjestu dobivaš uz aktualno pitanje:\n' + stories
    )


def route_position(point, route):
    """Return closest offset from a route and distance travelled along it, in metres."""
    latitude = math.radians(sum(p[1] for p in route)/len(route))
    scale_x, scale_y = 111320*math.cos(latitude), 111320
    projected = [(p[0]*scale_x, p[1]*scale_y) for p in route]
    target = (point[0]*scale_x, point[1]*scale_y)
    best_offset, best_along, travelled = float('inf'), 0, 0
    for start, end in zip(projected, projected[1:]):
        dx, dy = end[0]-start[0], end[1]-start[1]
        length = math.hypot(dx, dy)
        factor = max(0, min(1, ((target[0]-start[0])*dx+(target[1]-start[1])*dy)/(length**2))) if length else 0
        closest = (start[0]+factor*dx, start[1]+factor*dy)
        offset = math.dist(target, closest)
        if offset < best_offset:
            best_offset, best_along = offset, travelled+factor*length
        travelled += length
    return best_offset, best_along


def route_story_places(all_places, route, stories, max_offset=70):
    """Select pre-researched, visible POIs near the actual walking line in walking order."""
    selected = []
    for place_id, story in stories.items():
        place = all_places.get(place_id)
        if not place:
            continue
        offset, along = route_position(place['coordinates'], route)
        if offset <= max_offset:
            selected.append({**place, **story, 'offset_m': round(offset),
                             'along_m': round(along), 'trigger_m': min(85, max(45, round(offset+25)))})
    return sorted(selected, key=lambda place: place['along_m'])


def units(text, limit=3):
    # A sentence is a candidate unit, not a guarantee of semantic completeness.
    pieces = [s.strip() for s in re.split(r'(?<=[.!?])\s+(?=[A-ZČĆŽŠĐ])', text) if s.strip()]
    return pieces if limit is None else pieces[:limit]


def narrative_chunk(text, sentence_limit=4, word_limit=90):
    """Keep a small fact group for one spoken turn; retain the full source separately."""
    selected = []
    for sentence in units(text, None):
        if selected and (len(selected) >= sentence_limit
                         or len((' '.join(selected)+' '+sentence).split()) > word_limit):
            break
        selected.append(sentence)
    return ' '.join(selected)


def researched_script(card):
    text = re.sub(r'\[([^\]]+)\]\(https?://[^)]+\)', r'\1', card.get('text', ''))
    return re.sub(r'[^]*', '', text).strip()


class Session:
    def __init__(self, provider, places, realtime=False):
        self.id = secrets.token_urlsafe(24)
        self.provider, self.places = provider, places
        self.queue = asyncio.Queue(maxsize=256)
        self.events = deque(maxlen=1500)
        self.seq = 0
        self.epoch = 0
        self.foreground = None
        self.foreground_kind = None
        self.input_generation = 0
        self.transcription = None
        self.jobs = set()
        self.cards = {}
        self.active = places[0]
        self.gps = None
        self.last_gps_seq = -1
        self.muted = False
        self.auto = True
        self.paused = False
        self.short = False
        self.entered_at = None
        self.announced = set()
        self.played = {}
        self.history = deque(maxlen=8)
        self.research_target = None
        self.research_question = None
        self.intent_target = None
        self.closed = False
        self.research_count = 0
        self.research_versions = {}
        self.pending_research = None
        self.speech_cache = {}
        self.warm_enabled = False
        self.created = time.monotonic()
        self.created_at = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
        self.landmark_distances = {}
        self.story_target = None
        self.pending_landmark = None
        self.realtime = realtime
        self.current_street = street_for(places[0]['coordinates'])
        self.interrupted_story = None
        self.interrupted_utterance_id = None
        self.resume_offer_state = None
        self.pending_realtime_text = None
        self.paused_response = None
        self.manual_resume_pending = False
        self.position_context = None
        self.last_context_position = None
        self.last_context_at = 0
        self.research_tasks = {}
        self.research_slots = asyncio.Semaphore(2)
        self.auto_research_requested = set()
        self.landmarks_inside = set()
        self.landmarks_seen = set()
        self.pending_departure_notices = {}
        self.speaking_target = None
        self.last_spoken_target = None
        self.question_topic_target = None
        self.user_speaking = False
        self.waiting_for_transcript_until = 0
        self.continuation_offer = None
        self.last_continuation_offer = None
        self.continuation_offered_places = set()
        self.emit('session', provider=provider.name, models=provider.models,
                  voice=provider.voice, configured=provider.ready,
                  transport='webrtc' if realtime else 'buffered_tts',
                  street=self.current_street)

    def emit(self, kind, **data):
        self.seq += 1
        event = {'seq': self.seq, 'kind': kind, 'server_ms': time.monotonic()*1000,
                 'at': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                 'epoch': self.epoch, 'session_id': self.id, **data}
        self.events.append({k: v for k, v in event.items() if k != 'audio'})
        if not self.closed:
            try:
                self.queue.put_nowait(event)
            except asyncio.QueueFull:
                self.closed = True  # Do not block the location/control loop.
        return event

    def monitor_snapshot(self):
        latest_gps = next((event for event in reversed(self.events)
                           if event['kind'] == 'gps_processed'), None)
        return {
            'id': self.id,
            'created_at': self.created_at,
            'closed': self.closed,
            'active_landmark': {'id': self.active.get('id'), 'name': self.active.get('name')},
            'latest_gps': latest_gps,
            'conversation_running': (bool(self.foreground and not self.foreground.done()) or
                                     any(unit['state'] in {'queued', 'started'}
                                         for unit in self.played.values())),
            'transcription_running': bool(self.transcription and not self.transcription.done()),
            'research_running': any(not task.done() for task in self.jobs
                                    if getattr(task.get_coro(), '__name__', '') == 'research'),
            'research_target': self.research_target,
            'events': list(self.events)[-600:],
        }

    def task(self, coro):
        task = asyncio.create_task(coro)
        self.jobs.add(task)
        task.add_done_callback(self.jobs.discard)
        return task

    def speech_for(self, text):
        cached = self.speech_cache.get(text)
        if cached is None:
            cached = self.task(self.provider.synthesize(text))
            self.speech_cache[text] = cached

            def finished(task):
                if not task.cancelled() and task.exception() is not None:
                    if self.speech_cache.get(text) is task:
                        self.speech_cache.pop(text, None)
            cached.add_done_callback(finished)
        return cached

    def warm_start(self):
        self.warm_enabled = True

    @staticmethod
    def address_text(place):
        address = place.get('address')
        return address.rstrip('.') + '.' if address else ''

    async def close(self):
        self.closed = True
        for task in self.jobs:
            task.cancel()
        await asyncio.gather(*list(self.jobs), return_exceptions=True)

    def new_turn(self):
        if self.foreground and not self.foreground.done():
            self.foreground.cancel()
        self.epoch += 1
        self.paused = False
        self.emit('clear')

    @staticmethod
    def remaining_story(text, heard=''):
        """Skip complete or recognizably started facts instead of repeating them."""
        heard_normalized = normalize(heard)
        remaining = []
        for fact in units(text, None):
            words = re.findall(r'[a-z0-9]+', normalize(fact))
            signature = ' '.join(words[:min(6, len(words))])
            if normalize(fact) in heard_normalized or (len(words) >= 4 and signature in heard_normalized):
                continue
            remaining.append(fact)
        return ' '.join(remaining)

    def realtime_session_update(self):
        self.emit('realtime_session_update', instructions=realtime_instructions(
            self.places, self.current_street, self.interrupted_story, self.position_context))

    def publish_position_context(self, snapshot):
        """Send every sample to the context loop, throttling full prompt replacement."""
        self.position_context = snapshot
        previous = self.last_context_position
        now = time.monotonic()
        update = (not previous or snapshot['poi_id'] != previous['poi_id']
                  or snapshot['street'] != previous['street']
                  or distance(snapshot['coordinates'], previous['coordinates']) >= 8
                  or now-self.last_context_at >= 3)
        instructions = None
        if update:
            self.last_context_position = dict(snapshot)
            self.last_context_at = now
            instructions = realtime_instructions(
                self.places, self.current_street, self.interrupted_story, snapshot)
        self.emit('realtime_context', position=snapshot, instructions=instructions,
                  model_update=update)

    def research_place_for_text(self, text):
        normalized = normalize(text)
        if 'mladih' in normalized or re.search(r'\bzkm\b', normalized):
            target = next((place for place in self.places if place['id'] == 'culture-89'), None)
            if target:
                return target
        aliases = {'paviljon': 'osm-way-392439336', 'kazalist': 'culture-86',
                   'hnk': 'culture-86', 'tesl': 'osm-way-581658851',
                   'kovacic': 'culture-195', 'zkm': 'culture-89',
                   'stup': 'osm-node-1896949794', 'mazuranic': 'osm-node-1509283469'}
        for alias, place_id in aliases.items():
            if alias in normalized:
                target = next((place for place in self.places if place['id'] == place_id), None)
                if target:
                    return target
        if self.is_topic_followup(text):
            topic = self.question_topic_target or self.speaking_target or self.last_spoken_target
            target = next((place for place in self.places
                           if place['id'] == (topic or {}).get('id')), None)
            if target:
                return target
        return self.active

    @staticmethod
    def is_topic_followup(text):
        normalized = normalize(text)
        return any(phrase in normalized for phrase in
                   ('malo vise', 'reci vise', 'ispricaj vise', 'vise o tome',
                    'jos o tome', 'objasni detaljnije', 'reci jos', 'ispricaj jos',
                    'nastavi o tome', 'nastavi pricu'))

    @staticmethod
    def needs_web_research(text):
        normalized = normalize(text)
        return any(term in normalized for term in
                   ('internet', 'pretraz', 'provjeri', 'potrazi', 'pronadi na web',
                    'najnovij', 'trenutno otvor', 'danas otvor', 'sto se danas',
                    'koje izlozbe', 'koja izlozba'))

    def clear_interrupted_story(self, reason):
        previous = self.interrupted_story
        self.interrupted_story = None
        self.interrupted_utterance_id = None
        self.resume_offer_state = None
        self.emit('resume_topic_cleared', reason=reason,
                  poi_id=(previous or {}).get('id'), poi_name=(previous or {}).get('name'))
        self.realtime_session_update()

    def continuation_is_relevant(self, place_id):
        if not self.position_context:
            return False
        place = next((place for place in self.places if place['id'] == place_id), None)
        if not place:
            return False
        point = self.position_context['coordinates']
        if distance(point, place['coordinates']) > place.get('trigger_m', 60)+20:
            return False
        index = self.places.index(place)
        return not any(next_place['id'] in self.landmarks_inside
                       for next_place in self.places[index+1:])

    def clear_continuation_offer(self, reason, *, interrupt=True):
        offer = self.continuation_offer
        if not offer:
            return
        self.continuation_offer = None
        active_offer = [unit for unit in self.played.values()
                        if unit.get('purpose') == 'story_continuation_offer'
                        and unit['state'] in {'queued', 'started'}]
        for unit in active_offer if interrupt else []:
            unit['state'] = 'partial'
            unit['cancel_reason'] = reason
        if active_offer and interrupt:
            self.emit('clear')
        self.emit('continuation_cleared', poi_id=offer['id'], poi_name=offer['name'], reason=reason)

    def maybe_offer_story_continuation(self, unit):
        if (unit.get('purpose') not in {'story', 'story_resume'}
                or not unit.get('audio_started') or not unit.get('heard_text', '').strip()
                or unit.get('poi_id') in self.continuation_offered_places
                or self.pending_landmark or self.interrupted_story or self.muted
                or self.paused or not self.continuation_is_relevant(unit.get('poi_id'))):
            return
        remaining = unit.get('source_remainder', '')
        if not remaining:
            return
        place = {'id': unit['poi_id'], 'name': unit['poi_name']}
        self.continuation_offer = {**place, 'remaining_text': remaining, 'state': 'offering'}
        self.continuation_offered_places.add(place['id'])
        self.emit('continuation_offered', poi_id=place['id'], poi_name=place['name'])
        self.emit_realtime_prompt(
            f"Izgovori samo jedno kratko pitanje: „Želiš li čuti još o {place['name']}?“ "
            'Zatim šuti i čekaj korisnikov odgovor. Ne započinji novu priču i ne ponavljaj činjenice.',
            'story_continuation_offer', poi=place)

    def queue_landmark_transition(self, place, meters, movement):
        """Remember the next place without treating route progress as a user interruption."""
        if self.pending_landmark and self.pending_landmark['place']['id'] == place['id']:
            self.pending_landmark.update(meters=meters, movement=movement)
            return
        previous = self.story_target
        self.pending_landmark = {
            'place': place, 'meters': meters, 'movement': movement,
            'from': dict(previous) if previous else None,
        }
        self.emit('story_transition_queued',
                  from_poi_id=(previous or {}).get('id'),
                  from_poi_name=(previous or {}).get('name'),
                  to_poi_id=place['id'], to_poi_name=place['name'],
                  distance_m=round(meters, 1), reason='next_landmark')

    def start_landmark_story(self, place, meters, movement, transition_from=None):
        if self.interrupted_story and self.interrupted_story.get('id') != place['id']:
            self.clear_interrupted_story('starting_different_landmark')
        relation = self.landmark_relation(place, meters)
        meters = relation['distance_m']
        self.announced.add(place['id'])
        self.epoch += 1
        self.paused = False
        self.story_target = {'id': place['id'], 'name': place['name'],
                             'coordinates': place['coordinates'], 'left': False}
        if transition_from:
            self.emit('story_transitioned',
                      from_poi_id=transition_from.get('id'),
                      from_poi_name=transition_from.get('name'),
                      to_poi_id=place['id'], to_poi_name=place['name'],
                      distance_m=round(meters, 1),
                      reason='previous_landmark_left' if transition_from.get('left')
                      else 'previous_story_completed')
        self.emit('story_triggered', poi_id=place['id'], poi_name=place['name'],
                  distance_m=round(meters, 1), movement=movement,
                  reason='automatic_proximity')
        context = {
            'place': place['name'], 'look_for': place.get('look_for'),
            'sources': [{'url': place.get('source_url')}],
            'target_place': place,
            'target_position': relation,
        }
        if transition_from:
            context['transition_from'] = transition_from
        self.launch_answer('Nastavi priču uz rutu.', direct=place['story'],
                           context=context, kind='story')

    def landmark_relation(self, place, fallback_meters=0):
        point = (self.position_context or {}).get('coordinates')
        meters = distance(point, place['coordinates']) if point else fallback_meters
        index = self.places.index(place)
        active_index = next((i for i, item in enumerate(self.places)
                             if item['id'] == self.active['id']), index)
        if place['id'] in self.landmarks_inside or meters <= place.get('trigger_m', 60):
            state = 'nearby'
        elif place['id'] in self.landmarks_seen or active_index > index:
            state = 'passed'
        else:
            state = 'approaching'
        return {'poi_id': place['id'], 'poi_name': place['name'], 'state': state,
                'distance_m': round(meters, 1),
                'sample_seq': (self.position_context or {}).get('sample_seq')}

    def flush_departure_notices(self):
        if any(unit['state'] in {'queued', 'started'} for unit in self.played.values()):
            return
        for place_id, notice in list(self.pending_departure_notices.items()):
            self.pending_departure_notices.pop(place_id, None)
            self.emit('realtime_landmark_notice', poi_id=place_id,
                      poi_name=notice['name'], position=self.position_context,
                      text=f"Korisnik je napustio znamenitost {notice['name']}. "
                           'Prethodni govor je sada dovršen. Ovo je samo sistemska lokacijska '
                           'obavijest, nije korisnikova poruka ni zahtjev da nastaviš. '
                           'Ne odgovaraj na ovu obavijest i ne započinji govor zbog nje.')

    def update_landmark_zones(self, distances, snapshot):
        """Zone signals describe each landmark, not whichever point happens to be nearest."""
        for place in self.places:
            enter_m = place.get('trigger_m', 60)
            exit_m = enter_m+20
            meters = distances[place['id']]
            was_inside = place['id'] in self.landmarks_inside
            if not was_inside and meters <= enter_m:
                self.landmarks_inside.add(place['id'])
                self.landmarks_seen.add(place['id'])
                if self.story_target and self.story_target['id'] == place['id']:
                    self.story_target['left'] = False
                    self.story_target['departure_notified'] = False
                self.emit('landmark_entered', poi_id=place['id'], poi_name=place['name'],
                          distance_m=round(meters, 1), enter_m=enter_m, position=dict(snapshot))
                offer = self.continuation_offer
                if offer and place['id'] != offer['id']:
                    offer_index = next((i for i, item in enumerate(self.places)
                                        if item['id'] == offer['id']), -1)
                    if self.places.index(place) > offer_index:
                        self.clear_continuation_offer('next_landmark_entered', interrupt=False)
            elif was_inside and meters > exit_m:
                self.landmarks_inside.remove(place['id'])
                is_story = bool(self.story_target and self.story_target['id'] == place['id'])
                if is_story:
                    self.story_target['left'] = True
                self.emit('landmark_left', poi_id=place['id'], poi_name=place['name'],
                          distance_m=round(meters, 1), exit_m=exit_m,
                          was_story_target=is_story, position=dict(snapshot))
                self.pending_departure_notices[place['id']] = {'name': place['name']}
                if self.continuation_offer and self.continuation_offer['id'] == place['id']:
                    self.clear_continuation_offer('landmark_left', interrupt=False)

    def retire_departed_story(self):
        """Finish the current short chunk after departure; never cut its playback."""
        if (not self.story_target or not self.story_target.get('left')
                or self.story_target.get('departure_notified')):
            return False
        target_id = self.story_target['id']
        active = [unit for unit in self.played.values()
                  if unit.get('purpose') in {'story', 'story_resume'}
                  and unit.get('poi_id') == target_id
                  and unit['state'] in {'queued', 'started'}]
        foreground = bool(self.foreground and not self.foreground.done()
                          and self.foreground_kind == 'story')
        if not active and not foreground:
            return False
        self.story_target['departure_notified'] = True
        self.emit('story_departed', poi_id=target_id, poi_name=self.story_target['name'],
                  position=self.position_context, reason='landmark_left_finish_current_chunk',
                  continue_playback=True, resume_offer=False)
        return True

    def maybe_start_pending_landmark(self):
        pending = self.pending_landmark
        if (not pending or self.muted or self.paused or self.closed or self.interrupted_story
                or self.user_speaking or time.monotonic() < self.waiting_for_transcript_until
                or self.pending_realtime_text):
            return False
        active_audio = [unit for unit in self.played.values()
                        if unit['state'] in {'queued', 'started'} and unit['epoch'] == self.epoch]
        if active_audio or (self.foreground and not self.foreground.done()):
            return False
        self.pending_landmark = None
        self.start_landmark_story(pending['place'], pending['meters'], pending['movement'],
                                  pending.get('from'))
        return True

    def manual_resume(self):
        self.muted = False
        self.paused = False
        paused = self.paused_response
        if paused and not paused.get('cancelled'):
            self.manual_resume_pending = True
            self.emit('resume_waiting', reason='awaiting_cancelled_response',
                      utterance_id=paused.get('utterance_id'))
            return
        self._deliver_manual_resume()

    def _deliver_manual_resume(self):
        paused = self.paused_response
        self.manual_resume_pending = False
        if not paused:
            self.emit('resume', reason='manual_control')
            return
        self.paused_response = None
        place = {'id': paused.get('poi_id'), 'name': paused.get('poi_name')}
        remaining = paused.get('remaining_text', '')
        if paused.get('text'):
            if remaining:
                self.emit_realtime_prompt(
                    f"Nastavi zaustavljenu priču o mjestu {place['name']} samo od ovog još "
                    f"neizgovorenog dijela, bez ponavljanja: {remaining}",
                    'story_resume', text=remaining, poi=place)
        else:
            heard = paused.get('heard_text', '')
            self.emit_realtime_prompt(
                'Nastavi prethodni prekinuti odgovor od prve misli koju korisnik još nije čuo. '
                'WebRTC razgovor već sadrži samo odslušani dio, pa se osloni na taj kontekst i '
                f'ni slučajno ne ponavljaj ranije rečenice. Zadnji zabilježeni odslušani dio: {heard}',
                'answer_resume', poi=place)
        self.emit('resume', reason='manual_control',
                  poi_id=place.get('id'), poi_name=place.get('name'))

    def capture_manual_response(self):
        candidates = [(uid, unit) for uid, unit in self.played.items()
                      if unit.get('state') in {'queued', 'started'}]
        if not candidates:
            self.paused_response = None
            return None
        uid, unit = candidates[-1]
        self.paused_response = {
            'utterance_id': uid, 'purpose': unit.get('purpose'),
            'poi_id': unit.get('poi_id'), 'poi_name': unit.get('poi_name'),
            'text': unit.get('text', ''), 'instructions': unit.get('instructions', ''),
            'heard_text': '', 'remaining_text': unit.get('text', ''), 'cancelled': False,
            'unconfirmed_remainder': unit.get('source_remainder', '')
            if unit.get('audio_started') else unit.get('source_text', unit.get('text', '')),
        }
        self.emit('response_paused', utterance_id=uid, purpose=unit.get('purpose'),
                  poi_id=unit.get('poi_id'), poi_name=unit.get('poi_name'))
        return self.paused_response

    def capture_realtime_story_interruption(self, reason='user'):
        # Preserve the topic actually interrupted while GPS and transcription
        # continue independently; a queued future landmark must not replace it.
        if self.interrupted_story and self.resume_offer_state == 'awaiting_question':
            return self.interrupted_story
        candidates = [(uid, unit) for uid, unit in self.played.items()
                      if unit.get('purpose') in {'story', 'story_resume'}
                      and unit.get('audio_started')
                      and unit.get('state') in {'queued', 'started'}]
        if not candidates:
            return None
        speaking_uid = (self.speaking_target or {}).get('utterance_id')
        uid, unit = next((item for item in candidates if item[0] == speaking_uid), candidates[-1])
        if self.interrupted_utterance_id == uid:
            return self.interrupted_story
        full_text = unit.get('source_text') or unit.get('text', '')
        self.interrupted_utterance_id = uid
        self.interrupted_story = {
            'id': unit.get('poi_id'), 'name': unit.get('poi_name'),
            'full_text': full_text, 'heard_text': '',
            'remaining_text': unit.get('source_remainder', '')
            if unit.get('audio_started') else full_text,
            'reason': reason,
        }
        self.resume_offer_state = 'awaiting_question'
        self.emit('story_interrupted_by_user', utterance_id=uid,
                  from_poi_id=unit.get('poi_id'), from_poi_name=unit.get('poi_name'),
                  reason=reason)
        self.realtime_session_update()
        return self.interrupted_story

    def emit_realtime_prompt(self, instructions, purpose='answer', *, text='', poi=None):
        uid = secrets.token_hex(10)
        place = poi or self.active
        if purpose in {'answer', 'answer_with_resume_offer'}:
            question = next((item['text'] for item in reversed(self.history)
                             if item['role'] == 'user'), '')
            place = self.research_place_for_text(question)
            normalized = normalize(question).strip(' .!?')
            position_question = (any(term in normalized for term in
                                     ('gdje smo', 'gdje se', 'gdje sam', 'di smo', 'di se', 'kojoj ulici'))
                                 or normalized in {'a sada', 'a sad'})
            if position_question:
                place = self.active
                instructions = ('Odgovori samo aktualnom ulicom i najbližim mjestom iz najnovijeg '
                                'položaja. Raniji odgovori o tome gdje smo više ne vrijede. '
                                'Bez povijesne priče, smjerova i ponude ulaska. ' + instructions)
            else:
                instructions = (f"Pitanje se odnosi na: {place['name']}. "
                                f"Provjerene činjenice za to mjesto: {place.get('story', '')}\n"
                                'Odgovori izravno na pitanje u 1 do 3 rečenice. Ako pita kada je '
                                'nešto napravljeno, razlikuj osnivanje ustanove od gradnje zgrade; '
                                'ne zamjenjuj to uputama kako doći tamo i ne izmišljaj datum. '
                                + instructions)
                if self.is_topic_followup(question):
                    instructions = (
                        f"Korisnik traži više o temi koju je upravo slušao: {place['name']}. "
                        'Nastavi istu temu samo dodatnim, još neizgovorenim detaljima. '
                        'Promjena GPS položaja nije promjena korisnikove teme. '
                        'Ne prebacuj se na najbližu sljedeću znamenitost.\n' + instructions)
            instructions = f'Korisnik je upravo pitao: „{question}“.\n' + instructions
        source_text = text
        if purpose == 'story_resume' and text:
            text = narrative_chunk(text)
            instructions = instructions.replace(source_text, text)
            instructions += (' Najviše 4 do 5 kraćih povezanih rečenica, ukupno najviše 75 riječi, '
                             'zatim završi ovu cjelinu.')
        if self.position_context:
            instructions = location_brief(self.position_context) + '\n' + instructions
        self.played[uid] = {'text': text, 'state': 'queued', 'epoch': self.epoch,
                            'purpose': purpose, 'poi_id': place.get('id'),
                            'poi_name': place.get('name'), 'sources': [],
                            'instructions': instructions}
        if source_text:
            self.played[uid]['source_text'] = source_text
            self.played[uid]['source_remainder'] = (
                source_text[len(text):].strip() if source_text.startswith(text) else '')
        self.emit('realtime_prompt', utterance_id=uid, text=text, purpose=purpose,
                  poi_id=place.get('id'), poi_name=place.get('name'),
                  instructions=instructions, resume_from=self.interrupted_story)
        return uid

    def handle_realtime_text(self, text, *, remember=True):
        """Classify the turn before the client creates exactly one response."""
        normalized = re.sub(r'[^a-z0-9]+', ' ', normalize(text)).strip()
        if remember:
            self.history.append({'role': 'user', 'text': text})
        offer = self.last_continuation_offer or self.continuation_offer
        if offer:
            yes = (normalized in {'da', 'moze', 'zelim', 'nastavi', 'ajde', 'mozes'}
                   or any(phrase in normalized for phrase in
                          ['zelim da nastavis', 'nastavi pricati', 'nastavi pricu', 'reci jos']))
            no = (normalized in {'ne', 'ne hvala', 'ne zelim', 'nemoj', 'ne treba', 'dosta'}
                  or normalized.startswith(('ne ', 'nemoj ', 'ne treba')))
            # An explicit yes answers the audible question, regardless of how far
            # the walker moved while VAD/transcription was finishing.
            relevant = bool(offer.get('remaining_text'))
            self.last_continuation_offer = None
            self.clear_continuation_offer('accepted' if yes and relevant else
                                          'declined' if no else 'changed_topic')
            if no:
                return
            if yes and relevant:
                place = next(place for place in self.places if place['id'] == offer['id'])
                self.emit('continuation_accepted', poi_id=place['id'], poi_name=place['name'])
                self.launch_answer('Nastavi samo novim detaljima.', direct=offer['remaining_text'],
                                   context={'target_place': place}, kind='story_resume')
                return
        self.capture_realtime_story_interruption('typed_question')
        pending = self.interrupted_story
        interrupted_unit = self.played.get(self.interrupted_utterance_id)
        if pending and (not interrupted_unit or not interrupted_unit.get('audio_started')
                        or interrupted_unit.get('poi_id') != pending.get('id')):
            self.clear_interrupted_story('no_matching_played_story')
            pending = None
        if pending and self.resume_offer_state == 'offered':
            no = (normalized in {'ne', 'ne hvala', 'ne zelim', 'nemoj', 'ne treba', 'dosta'}
                  or normalized.startswith(('ne ', 'nemoj ', 'ne treba')))
            yes = (normalized in {'da', 'moze', 'zelim', 'nastavi', 'ajde', 'mozes'}
                   or any(phrase in normalized for phrase in
                          ['zelim da nastavis', 'nastavi pricati', 'nastavi pricu']))
            if no:
                self.emit('resume_declined', poi_id=pending.get('id'),
                          poi_name=pending.get('name'))
                self.clear_interrupted_story('declined')
                self.maybe_start_pending_landmark()
                return
            if yes:
                remaining = pending.get('remaining_text', '')
                place = {'id': pending.get('id'), 'name': pending.get('name')}
                self.emit('resume_accepted', poi_id=place['id'], poi_name=place['name'])
                self.interrupted_story = None
                self.interrupted_utterance_id = None
                self.resume_offer_state = None
                self.realtime_session_update()
                if remaining:
                    self.emit_realtime_prompt(
                        f"Nastavi prekinutu priču o mjestu {place['name']} isključivo ovim još "
                        f"neizgovorenim dijelom, bez ponavljanja uvoda ili ranijih činjenica: "
                        f"{remaining}", 'story_resume', text=remaining, poi=place)
                return
            self.emit('resume_changed_topic', from_poi_id=pending.get('id'),
                      from_poi_name=pending.get('name'), text=text)
            self.clear_interrupted_story('changed_topic')
            pending = None
        target = self.research_place_for_text(text)
        self.intent_target = target['id']
        if self.needs_web_research(text):
            self.research_question, self.research_target = text, target['id']
            if pending:
                self.resume_offer_state = 'answering_question'
            self.start_research(text, target, explicit=True)
            return
        # A completed lookup remains useful context, but must not answer an older
        # question aloud after the user has moved to another conversational turn.
        self.research_question = None
        if pending:
            self.resume_offer_state = 'answering_question'
            self.emit_realtime_prompt(
                f"Najprije izravno odgovori na posljednje korisnikovo pitanje. Zatim jednom, kratko "
                f"pitaj: „Prekinuo si me dok sam pričao o {pending['name']}. Želiš li da "
                f"nastavim?“ Ne nastavljaj priču prije korisnikova odgovora.",
                'answer_with_resume_offer')
            return
        self.emit_realtime_prompt(
            'Odgovori prirodno na posljednju korisnikovu poruku koristeći cijeli dosadašnji '
            'razgovor i ne ponavljaj već izgovorene činjenice.', 'answer')

    def handle_realtime_event(self, event_kind, *, text='', utterance_id=None,
                              response_id=None, purpose=None):
        if event_kind == 'user_transcript':
            text = croatian_latin_text(text)
        unit = self.played.get(utterance_id)
        if (unit and unit.get('cancel_reason') and event_kind in
                {'assistant_generated', 'assistant_started', 'assistant_audio_started', 'assistant_completed'}):
            self.emit('realtime_event_discarded', event_kind=event_kind,
                      utterance_id=utterance_id, reason=unit['cancel_reason'])
            return
        if event_kind == 'assistant_generated' and unit:
            unit['generated_text'] = text
            unit['generation_complete'] = True
        if event_kind == 'assistant_audio_started' and unit:
            if unit.get('purpose') == 'story_continuation_offer' and self.continuation_offer:
                self.last_continuation_offer = dict(self.continuation_offer)
            if (unit.get('purpose') in {'story', 'story_resume'} and self.interrupted_story
                    and self.interrupted_utterance_id != utterance_id):
                self.clear_interrupted_story('new_story_became_audible')
            unit['state'] = 'started'
            unit['audio_started'] = True
            self.speaking_target = {'id': unit.get('poi_id'), 'name': unit.get('poi_name'),
                                    'utterance_id': utterance_id}
            self.last_spoken_target = dict(self.speaking_target)
        if unit and event_kind in {'assistant_started', 'assistant_completed',
                                   'assistant_cancelled', 'assistant_failed'}:
            unit['state'] = ('completed' if event_kind == 'assistant_completed'
                             else 'partial' if event_kind in {'assistant_cancelled', 'assistant_failed'}
                             else 'started')
        if event_kind == 'speech_started':
            self.user_speaking = True
            self.question_topic_target = dict(self.speaking_target or self.last_spoken_target or {})
            self.capture_realtime_story_interruption('microphone')
        elif event_kind == 'speech_stopped':
            self.user_speaking = False
            self.waiting_for_transcript_until = time.monotonic()+5
        elif event_kind == 'user_transcript' and text:
            self.user_speaking = False
            self.waiting_for_transcript_until = 0
            interrupted = self.played.get(self.interrupted_utterance_id)
            if interrupted and interrupted.get('state') in {'queued', 'started'}:
                self.history.append({'role': 'user', 'text': text})
                self.pending_realtime_text = text
            else:
                self.handle_realtime_text(text)
        elif (event_kind == 'assistant_cancelled' and unit and self.paused_response
              and self.paused_response.get('utterance_id') == utterance_id):
            paused = self.paused_response
            paused['cancelled'] = True
            paused['heard_text'] = text.strip()
            paused['remaining_text'] = (
                self.remaining_story(paused.get('text', ''), paused['heard_text'])
                if paused['heard_text'] else paused.get('unconfirmed_remainder', ''))
            self.emit('response_pause_ready', utterance_id=utterance_id,
                      purpose=paused.get('purpose'), remaining_text=paused['remaining_text'])
            self.emit('realtime_' + event_kind, text=text, response_id=response_id,
                      utterance_id=utterance_id, purpose=purpose)
            if self.manual_resume_pending:
                self._deliver_manual_resume()
            return
        elif (event_kind == 'assistant_cancelled' and unit
              and self.interrupted_utterance_id == utterance_id):
            heard = text.strip()
            self.interrupted_story['heard_text'] = heard
            self.interrupted_story['remaining_text'] = (
                self.remaining_story(unit.get('source_text') or unit.get('text', ''), heard)
                if heard else unit.get('source_remainder', '') if unit.get('audio_started')
                else unit.get('source_text') or unit.get('text', ''))
            self.emit('resume_available', poi_id=self.interrupted_story.get('id'),
                      poi_name=self.interrupted_story.get('name'),
                      remaining_text=self.interrupted_story.get('remaining_text'))
            self.realtime_session_update()
            # Let the client observe cancellation before it receives the next response.create.
            self.emit('realtime_' + event_kind, text=text, response_id=response_id,
                      utterance_id=utterance_id, purpose=purpose)
            pending_text, self.pending_realtime_text = self.pending_realtime_text, None
            if pending_text:
                self.handle_realtime_text(pending_text, remember=False)
            return
        elif event_kind == 'assistant_completed':
            if text:
                self.history.append({'role': 'assistant', 'text': text})
                if unit:
                    unit['heard_text'] = text
            if self.speaking_target and self.speaking_target.get('utterance_id') == utterance_id:
                self.speaking_target = None
            if purpose == 'answer_with_resume_offer' and self.interrupted_story:
                self.resume_offer_state = 'offered'
                self.emit('resume_offered', poi_id=self.interrupted_story.get('id'),
                          poi_name=self.interrupted_story.get('name'))
            if unit:
                self.maybe_offer_story_continuation(unit)
        self.emit('realtime_' + event_kind, text=text, response_id=response_id,
                  utterance_id=utterance_id, purpose=purpose)
        if event_kind in {'assistant_completed', 'assistant_failed'}:
            self.flush_departure_notices()
            self.maybe_deliver_research()
            self.maybe_start_pending_landmark()

    def launch_answer(self, question, *, direct=None, context=None, kind='answer'):
        if self.realtime:
            source_text = direct or ''
            text = narrative_chunk(source_text) if kind in {'story', 'story_resume'} else source_text
            target_place = (context or {}).get('target_place') or self.active
            transition_from = (context or {}).get('transition_from')
            if transition_from:
                instruction = (
                    f"Prethodna priča o mjestu {transition_from['name']} je dovršena. "
                    f"Jednom kratkom, prirodnom prijelaznom rečenicom usmjeri pažnju na mjesto "
                    f"{target_place['name']}. "
                )
            else:
                instruction = f"Usmjeri pažnju na mjesto {target_place['name']}. "
            if kind == 'story_resume':
                instruction += (f"Korisnik je izričito pristao čuti nastavak o {target_place['name']}. "
                                'Nastavi tu temu i kada smo se već udaljili. Taj izbor ima prednost '
                                'pred automatskim prijelazom GPS-a na drugo mjesto. ')
            relation = (context or {}).get('target_position') or self.landmark_relation(target_place)
            if relation['state'] == 'passed':
                instruction += ('Korisnik je ovu znamenitost već prošao. Uvod neka bude u prošlom '
                                'vremenu, primjerice „Već si prošao pokraj…“. Nemoj govoriti '
                                '„ulazimo“, „stižemo“ ili da je mjesto upravo ispred korisnika. ')
            elif relation['state'] == 'nearby':
                instruction += ('Korisnik je već uz ovu znamenitost. Reci „Sada si uz…“ ili '
                                'samo započni priču; nemoj najavljivati ulazak ni približavanje. ')
            else:
                instruction += ('Korisnik se tek približava ovom mjestu. Uvod uskladi s time; '
                                'nemoj tvrditi da je već stigao. ')
            instruction += f"Odnos prema ovoj znamenitosti upravo provjeren: {json.dumps(relation, ensure_ascii=False)}. "
            instruction += (
                'Ispričaj jednu kratku, povezanu cjelinu od 4 do 5 kraćih hrvatskih rečenica, '
                'ukupno najviše 75 riječi. Ne čitaj cijeli materijal niti dodaj nove činjenice. '
                'Nakon te cjeline stani i ostavi prostor korisniku. '
                f'Činjenice za ovu cjelinu: {text}')
            uid = self.emit_realtime_prompt(instruction, kind, text=text, poi=target_place)
            self.played[uid]['source_text'] = source_text
            self.played[uid]['source_remainder'] = (
                source_text[len(text):].strip() if source_text.startswith(text) else '')
            return
        epoch = self.epoch
        task = self.task(self.answer(question, epoch, direct, context, kind))
        self.foreground, self.foreground_kind = task, kind
        def finished(done):
            if self.foreground is done:
                self.foreground_kind = None
            self.maybe_deliver_research()
            self.maybe_start_pending_landmark()
        task.add_done_callback(finished)

    def invalidate_input(self):
        self.input_generation += 1
        if self.transcription and not self.transcription.done():
            self.transcription.cancel()

    def maybe_deliver_research(self):
        pending = self.pending_research
        if not pending or self.muted or self.paused or self.closed:
            return
        question, target, version, card = pending
        if (self.research_versions.get(target) != version or
            self.research_question != question or self.intent_target != target):
            self.pending_research = None
            return
        if self.foreground and not self.foreground.done():
            return
        if any(u['epoch'] == self.epoch and u['state'] in {'queued', 'started'} for u in self.played.values()):
            return
        self.pending_research = None
        if self.realtime:
            place = next((place for place in self.places if place['id'] == target), self.active)
            if card.get('failure_message'):
                return
            instructions = (
                'Odgovori sada na korisnikovo pitanje koristeći rezultat zasebnog istraživačkog '
                'loopa koji je dodan ovom razgovoru. Govori prirodno hrvatski, samo o potvrđenom '
                'sadržaju, bez citata, izvora, administrativnih fraza ili ponavljanja. '
                f'Pitanje: {question}\nProvjereni sadržaj: {researched_script(card)}')
            purpose = 'research_answer'
            if self.interrupted_story and self.resume_offer_state == 'answering_question':
                instructions += (f" Na kraju jednom kratko pitaj želi li korisnik da nastaviš "
                                 f"prekinutu priču o {self.interrupted_story['name']}.")
                purpose = 'answer_with_resume_offer'
            self.emit_realtime_prompt(instructions, purpose, poi=place)
            return
        self.launch_answer(question, context=card,
                           direct=card.get('failure_message') or researched_script(card))

    async def answer(self, question, epoch, direct=None, context=None, kind='answer'):
        try:
            context = {**(context or {}), 'heard_history': list(self.history),
                       'current_position': self.gps, 'short': self.short}
            text = direct if direct is not None else await self.provider.respond(question, context)
            pieces = units(text, None if kind == 'story' else 3)
            if self.short:
                pieces = pieces[:1]
            for index, piece in enumerate(pieces):
                cached = self.speech_cache.get(piece)
                self.emit('speech_lookup', cache_hit=bool(cached and cached.done()), text=piece)
                audio = await asyncio.shield(self.speech_for(piece))
                if self.closed or epoch != self.epoch or self.muted:
                    return
                uid = secrets.token_hex(10)
                self.played[uid] = {'text': piece, 'state': 'queued', 'epoch': epoch,
                                    'purpose': kind, 'poi_id': self.active['id'],
                                    'poi_name': self.active['name'],
                                    'sources': (context or {}).get('sources', [])}
                self.emit('audio', utterance_id=uid, text=piece, unit=index,
                          purpose=kind, poi_id=self.active['id'],
                          sources=(context or {}).get('sources', []),
                          provider=self.provider.name, model=self.provider.models['tts'],
                          voice=self.provider.voice,
                          audio=base64.b64encode(audio).decode(),
                          audio_sha256=hashlib.sha256(audio).hexdigest())
            if epoch == self.epoch:
                self.emit('answer_generated', question=question)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if epoch == self.epoch:
                self.emit('error', message=str(exc) if isinstance(exc, VoiceUnavailable)
                          else 'Odgovor trenutačno nije moguće pripremiti.')

    async def handle_audio(self, raw):
        generation = self.input_generation
        try:
            with wave.open(io.BytesIO(raw), 'rb') as wav:
                duration = wav.getnframes()/wav.getframerate()
                if not .08 <= duration <= 22 or wav.getnchannels() != 1:
                    raise ValueError('audio')
            self.emit('stt_started', audio_bytes=len(raw),
                      audio_sha256=hashlib.sha256(raw).hexdigest())
            text = await self.provider.transcribe(raw)
            if generation != self.input_generation:
                return
            if is_transcription_artifact(text):
                self.paused = False
                self.emit('transcript_discarded', text=text, provider=self.provider.name,
                          model=self.provider.models['stt'], reason='stt_prompt_echo')
                self.emit('resume', reason='transcript_artifact')
                return
            self.emit('transcript', text=text, provider=self.provider.name,
                      model=self.provider.models['stt'])
            self.handle_text(text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if generation != self.input_generation:
                return
            self.paused = False
            self.emit('clear')
            self.emit('error', message=str(exc) if isinstance(exc, VoiceUnavailable)
                      else 'Snimku nije moguće razumjeti. Pokušaj ponovno.')

    def handle_text(self, text):
        n = normalize(text).strip(' .!?')
        if n in {'aha', 'mhm', 'uhum', 'da', 'u redu'}:
            self.paused = False
            self.emit('resume', reason='backchannel')
            return
        self.new_turn()
        self.history.append({'role': 'user', 'text': text})
        if 'krace' in n:
            self.short = True
        if any(x in n for x in ['stani', 'zaustavi', 'prestani', 'idemo dalje']):
            self.muted = True
            self.emit('stopped', reason='user', research_continues=True)
            return
        self.muted = False
        target = next((p for p in self.places if
                       ('paviljon' in n and p['id'] == DEMO_IDS[2]) or
                       ('kazalist' in n and p['id'] == DEMO_IDS[0]) or
                       ('stup' in n and p['id'] == DEMO_IDS[1])), self.active)
        self.intent_target = target['id']
        if 'nastavi' in n and self.research_question:
            card = self.cards.get(self.research_target)
            if card and card.get('question') == self.research_question:
                self.pending_research = None
                self.launch_answer(text, direct=researched_script(card), context=card)
            return
        if 'adres' in n:
            self.emit('local_lookup', poi_id=target['id'], address=target.get('address'),
                      source_refs=target.get('source_refs', []))
            self.launch_answer(text, direct=self.address_text(target), context={'place': target['name'],
                'facts': {'address': target.get('address')},
                'sources': target.get('source_refs', [])})
            return
        if 'predstavi' in n:
            self.launch_answer(text, context={'place': target['name'],
                'facts': {'description': target.get('description')},
                'sources': target.get('source_refs', [])}, kind='story')
            return
        card = self.cards.get(target['id'])
        if card and not any(w in n for w in ['provjeri', 'pretrazi', 'internet']):
            self.launch_answer(text, context=card)
            return
        self.research_question, self.research_target = text, target['id']
        self.start_research(text, target, explicit=True)

    def start_research(self, question, place, explicit=False):
        target = place['id']
        key = (target, normalize(question))
        existing = self.research_tasks.get(key)
        if existing and not existing.done():
            self.emit('research_deduplicated', poi_id=target, question=question)
            return
        if self.research_count >= (24 if self.realtime else 8):
            self.emit('error', message='Dosegnut je broj provjera za ovu šetnju. Pokreni novu sesiju.')
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self.research_count += 1
        version = self.research_versions.get(target, 0) + 1
        self.research_versions[target] = version
        snapshot = dict(self.position_context) if self.position_context else None
        research_place = {**place, 'position_context': snapshot,
                          'conversation_context': list(self.history)}
        task = self.task(self.research(question, research_place, explicit, version))
        self.research_tasks[key] = task

    def prepare_nearby_research(self, nearest, distances):
        """Prefetch only curated route places, once each, beside the voice loop."""
        if not self.realtime or not self.auto or not self.provider.ready:
            return
        index = self.places.index(nearest)
        candidates = self.places[index:index+2]
        for place in candidates:
            if (not place.get('story') or distances[place['id']] > 180
                    or place['id'] in self.auto_research_requested
                    or any(key[0] == place['id'] and not task.done()
                           for key, task in self.research_tasks.items())):
                continue
            self.auto_research_requested.add(place['id'])
            self.start_research(
                f"Pronađi zanimljiv, potvrđen detalj o mjestu {place['name']} u Zagrebu koji "
                'nadopunjuje pripremljenu priču i pomaže osobi koja ga upravo promatra. '
                'Ne prepisuj kataloški opis, adresu ni administrativne podatke.',
                place, explicit=False)

    async def research(self, question, place, explicit, version):
        target_id = place['id']
        request_id = secrets.token_hex(8)
        self.emit('research_started', request_id=request_id, poi_id=target_id,
                  poi_name=place['name'], question=question, explicit=explicit,
                  position=place.get('position_context'))
        try:
            async with self.research_slots:
                card = await asyncio.wait_for(self.provider.research(question, place), 18)
            if self.research_versions.get(target_id) != version:
                self.emit('research_discarded', request_id=request_id, poi_id=target_id,
                          poi_name=place['name'], question=question)
                return
            card = {**card, 'question': question, 'research_version': version}
            self.cards[target_id] = card
            if self.warm_enabled:
                for piece in units(researched_script(card)):
                    self.speech_for(piece)
            self.emit('research_ready', request_id=request_id, poi_id=target_id,
                      poi_name=place['name'], question=question, card=card)
            if self.realtime:
                self.emit('realtime_research_result', request_id=request_id,
                          poi_id=target_id, poi_name=place['name'], question=question,
                          card=card, explicit=explicit, position=place.get('position_context'))
            if explicit:
                self.pending_research = (question, target_id, version, card)
                self.maybe_deliver_research()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.emit('research_failed', request_id=request_id, poi_id=target_id,
                      poi_name=place['name'], question=question,
                      message='Istraživanje nije dalo upotrebljivu priču.')

    def gps_update(self, data):
        seq = int(data['sample_seq'])
        point = [float(data['lon']), float(data['lat'])]
        if not all(math.isfinite(v) for v in point) or not(-180 <= point[0] <= 180 and -90 <= point[1] <= 90):
            raise ValueError('coordinates')
        if seq <= self.last_gps_seq:
            self.emit('gps_discarded', sample_seq=seq)
            return
        self.last_gps_seq = seq
        self.gps = data
        current_street = street_for(point)
        if current_street != self.current_street:
            previous_street, self.current_street = self.current_street, current_street
            self.emit('street_changed', street=current_street, previous_street=previous_street,
                      position=point)
        distances = {place['id']: distance(point, place['coordinates']) for place in self.places}
        nearest = min(self.places, key=lambda place: distances[place['id']])
        meters = distances[nearest['id']]
        previous = self.landmark_distances.get(nearest['id'])
        delta = None if previous is None else meters-previous
        movement = ('unknown' if delta is None else 'approaching' if delta < -.5
                    else 'leaving' if delta > .5 else 'steady')
        self.landmark_distances.update(distances)
        changed = nearest['id'] != self.active['id']
        if changed:
            self.active, self.entered_at = nearest, None
            self.emit('location_changed', poi_id=nearest['id'], poi_name=nearest['name'],
                      distance_m=round(meters, 1), movement=movement)
        self.emit('gps_processed', sample_seq=seq, position=point, poi_id=nearest['id'],
                  poi_name=nearest['name'], distance_m=round(meters, 1),
                  distance_delta_m=None if delta is None else round(delta, 1),
                  movement=movement, speed_mps=data.get('speed_mps'),
                  accuracy_m=data.get('accuracy_m', 0), street=current_street)
        if self.realtime:
            snapshot = {
                'sample_seq': seq, 'coordinates': point, 'street': current_street,
                'poi_id': nearest['id'], 'poi_name': nearest['name'],
                'distance_m': round(meters, 1), 'movement': movement,
                'speed_mps': data.get('speed_mps', 0),
                'accuracy_m': data.get('accuracy_m', 0),
                'position_mode': data.get('position_mode', 'simulated_demo'),
            }
            if self.story_target:
                spoken_place = next((place for place in self.places
                                     if place['id'] == self.story_target['id']), None)
                if spoken_place:
                    spoken_distance = distances[spoken_place['id']]
                    snapshot['story_target'] = {
                        'id': spoken_place['id'], 'name': spoken_place['name'],
                        'distance_m': round(spoken_distance, 1),
                        'state': 'left' if spoken_distance > spoken_place.get('trigger_m', 60)+20
                        else 'nearby',
                    }
            self.publish_position_context(snapshot)
            if float(data.get('accuracy_m', 999)) <= 15:
                self.update_landmark_zones(distances, snapshot)
                self.flush_departure_notices()
                departed = self.retire_departed_story()
                if departed and nearest['id'] != self.story_target['id']:
                    self.entered_at = time.monotonic()-2.1
            self.prepare_nearby_research(nearest, distances)
        if self.warm_enabled and meters <= 180:
            speeches = units(nearest.get('story', ''), None)
            if nearest.get('address'):
                speeches.append(self.address_text(nearest))
            for speech in speeches:
                if speech not in self.speech_cache:
                    self.emit('local_speech_prefetch', poi_id=nearest['id'],
                              source_refs=nearest.get('source_refs', []))
                    self.speech_for(speech)
        if not self.auto or not self.provider.ready:
            return
        if (self.realtime and not self.user_speaking and self.waiting_for_transcript_until
                and time.monotonic() >= self.waiting_for_transcript_until
                and not self.pending_realtime_text):
            self.waiting_for_transcript_until = 0
            if self.resume_offer_state == 'awaiting_question':
                self.clear_interrupted_story('speech_ended_without_transcript')
        if self.realtime and (self.user_speaking
                or time.monotonic() < self.waiting_for_transcript_until
                or self.pending_realtime_text or self.resume_offer_state == 'awaiting_question'):
            return
        accurate = float(data.get('accuracy_m', 999)) <= 15
        if meters <= nearest.get('trigger_m', 60) and accurate:
            self.entered_at = self.entered_at or time.monotonic()
            active_audio = [unit for unit in self.played.values()
                            if unit['state'] in {'queued', 'started'} and unit['epoch'] == self.epoch]
            story_busy = any(unit.get('purpose') in {'story', 'story_resume'}
                             for unit in active_audio)
            blocking_audio = any(unit.get('purpose') not in {'story', 'story_resume'}
                                 for unit in active_audio)
            foreground_busy = bool(self.foreground and not self.foreground.done())
            interruptible_story = story_busy or (foreground_busy and self.foreground_kind == 'story')
            if (time.monotonic()-self.entered_at >= 2 and not self.muted and not self.paused
                and nearest['id'] not in self.announced and not blocking_audio
                and (not foreground_busy or self.foreground_kind == 'story')):
                if interruptible_story and self.story_target:
                    self.queue_landmark_transition(nearest, meters, movement)
                else:
                    previous = (dict(self.story_target) if self.story_target
                                and self.story_target.get('left') else None)
                    self.start_landmark_story(nearest, meters, movement, previous)
        else:
            self.entered_at = None


def make_router(scene, provider_factory=OpenAIProvider, route_coordinates=None):
    router = APIRouter(prefix='/voice')
    all_places = {f['properties']['id']: {**f['properties'], 'coordinates': f['geometry']['coordinates']}
                  for f in scene['features']}
    stories = json.loads((BASE/'data/voice-route-stories.json').read_text())
    sessions = {}
    archived_sessions = deque(maxlen=8)
    if isinstance(route_coordinates, dict):
        route_cache = {key: value for key, value in route_coordinates.items()}
    elif route_coordinates is not None:
        route_cache = {'green-horseshoe': route_coordinates}
    else:
        route_cache = {}
    demo_contexts = {}
    route_locks = {demo_id: asyncio.Lock() for demo_id in DEMO_CATALOG}

    def demo_summary(demo_id):
        spec = DEMO_CATALOG[demo_id]
        return {'id': demo_id, 'name': spec['name'], 'summary': spec['summary'],
                'place_count': len(spec['places'])}

    async def ensure_demo(demo_id):
        if demo_id not in DEMO_CATALOG:
            raise HTTPException(404, 'Ova demo-ruta nije dostupna.')
        async with route_locks[demo_id]:
            if demo_id in demo_contexts:
                return demo_contexts[demo_id]
            spec = DEMO_CATALOG[demo_id]
            route = route_cache.get(demo_id)
            if route is None:
                try:
                    anchors = [all_places[key]['coordinates'] for key in spec['anchors']]
                    async with httpx.AsyncClient(timeout=12) as client:
                        from os import environ
                        res = await client.post(environ.get('LAGANO_GH_URL', 'http://127.0.0.1:8314')+'/route', json={
                            'profile': 'foot', 'points': anchors,
                            'points_encoded': False, 'instructions': False, 'ch.disable': True})
                    res.raise_for_status()
                    route = res.json()['paths'][0]['points']['coordinates']
                    if not isinstance(route, list) or len(route) < 2:
                        raise ValueError('route')
                    route_cache[demo_id] = route
                except Exception as exc:
                    raise HTTPException(503, 'Pješačku rutu nije moguće pripremiti.') from exc
            selected = [place for place in route_story_places(all_places, route, stories)
                        if place['id'] in spec['places']]
            selected_by_id = {place['id']: place for place in selected}
            missing = [place_id for place_id in spec['places'] if place_id not in selected_by_id]
            if missing:
                raise HTTPException(503, 'Sve provjerene priče uz ovu rutu nisu dostupne.')
            selected = [place for place in selected if place['id'] in spec['places']]
            for place in selected:
                if place['id'] == 'culture-86':
                    place['name'] = 'HNK i Zdenac života'
            distance_m = round(sum(distance(a, b) for a, b in zip(route, route[1:])))
            context = {**demo_summary(demo_id), 'demo': True,
                       'distance_m': distance_m,
                       'estimated_minutes': max(1, round(distance_m/80)),
                       'geometry': {'type': 'LineString', 'coordinates': route},
                       'places': selected, 'route_source': 'GraphHopper/OpenStreetMap',
                       'field_verified': False}
            demo_contexts[demo_id] = context
            return context

    @router.get('/')
    async def page():
        return FileResponse(BASE/'voice-ui/index.html', headers={'Cache-Control': 'no-store'})

    @router.get('/monitor')
    async def monitor_page():
        return FileResponse(BASE/'voice-ui/monitor.html', headers={'Cache-Control': 'no-store'})

    @router.get('/assets/{name}')
    async def asset(name: str):
        if name in {'leaflet.js', 'leaflet.css', 'manrope-font.woff2'}:
            return FileResponse(BASE/'runtime/wordpress/wp-content/plugins/lagano-walks/assets'/name)
        if name not in {'guide.js', 'guide.css', 'tap.js', 'monitor.js', 'monitor.css'}:
            raise HTTPException(404)
        return FileResponse(BASE/'voice-ui'/name, headers={'Cache-Control': 'no-store'})

    @router.get('/monitor/state')
    async def monitor_state():
        active = [session.monitor_snapshot() for session in sessions.values()]
        return {'at': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                'sessions': active + list(archived_sessions)}

    @router.get('/status')
    async def status():
        p = provider_factory()
        return {'provider': p.name, 'configured': p.ready, 'models': p.models, 'voice': p.voice,
                'live_validated': False, 'scope': 'T03/T08/T06 pilot'}

    @router.get('/places')
    async def landmarks():
        return (await ensure_demo('green-horseshoe'))['places']

    @router.get('/route')
    async def route():
        context = await ensure_demo('green-horseshoe')
        return {'coordinates': context['geometry']['coordinates'],
                'source': 'GraphHopper/OpenStreetMap',
                'field_verified': False}

    @router.get('/demos')
    async def demos():
        return {'demos': [demo_summary(demo_id) for demo_id in DEMO_CATALOG]}

    @router.get('/demos/{demo_id}')
    async def demo(demo_id: str):
        return await ensure_demo(demo_id)

    @router.post('/context')
    async def context():
        raise HTTPException(422, 'Ovaj dio rute još nije mapiran za glasovni vodič. '
                                'Odaberi jednu od pripremljenih demo-ruta.')

    @router.post('/sessions')
    async def create(request: Request):
        for sid, sess in list(sessions.items()):
            if sess.closed or time.monotonic()-sess.created > 1800:
                await sess.close()
                del sessions[sid]
        if len(sessions) >= 16:
            raise HTTPException(429, 'Previše aktivnih šetnji. Pokušaj poslije.')
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            body = {}
        realtime = body.get('mode') == 'realtime'
        if 'route' in body:
            raise HTTPException(422, 'Proizvoljna ruta nije dostupna u glasovnom demu.')
        demo_id = body.get('demo_id')
        if not isinstance(demo_id, str):
            raise HTTPException(422, 'Odaberi pripremljenu demo-rutu.')
        selected_context = await ensure_demo(demo_id)
        session_places = selected_context['places']
        route_name = selected_context['name']
        session = Session(provider_factory(), session_places, realtime=realtime)
        if not realtime:
            session.warm_start()
        sessions[session.id] = session
        return {'id': session.id, 'provider': session.provider.name,
                'mode': 'realtime' if realtime else 'legacy',
                'demo_id': demo_id,
                'route_name': route_name,
                'realtime_model': session.provider.models.get('realtime'),
                'instructions': realtime_instructions(session_places, session.current_street)
                if realtime else None}

    @router.post('/realtime/{sid}')
    async def realtime_call(sid: str, request: Request):
        session = sessions.get(sid)
        origin = request.headers.get('origin')
        host = request.headers.get('host')
        if (not session or session.closed or not session.realtime or
            (origin and origin.split('://', 1)[-1] != host)):
            raise HTTPException(403, 'Realtime sesija nije dostupna.')
        raw = await request.body()
        if not 0 < len(raw) <= 65536:
            raise HTTPException(413, 'SDP ponuda nije valjana.')
        try:
            sdp = raw.decode()
        except UnicodeDecodeError as exc:
            raise HTTPException(400, 'SDP ponuda nije valjana.') from exc
        answer, location = await session.provider.realtime_call(
            sdp, realtime_instructions(session.places, session.current_street,
                                       session.interrupted_story, session.position_context))
        session.emit('realtime_connected', model=session.provider.models['realtime'],
                     voice=session.provider.voice)
        headers = {'Cache-Control': 'no-store'}
        if location:
            headers['Location'] = location
        return Response(answer, media_type='application/sdp', headers=headers)

    @router.websocket('/ws/{sid}')
    async def connection(ws: WebSocket, sid: str):
        session = sessions.get(sid)
        origin = ws.headers.get('origin')
        if not session or session.closed or (origin and origin.split('://', 1)[-1] != ws.headers.get('host')):
            await ws.close(code=1008)
            return
        await ws.accept()

        async def send():
            while not session.closed:
                await ws.send_json(await session.queue.get())

        sender = asyncio.create_task(send())
        try:
            while not session.closed:
                raw = await ws.receive_text()
                if len(raw) > 2_000_000:
                    await ws.close(code=1009)
                    break
                try:
                    msg = json.loads(raw)
                    kind = msg.get('type')
                    if kind == 'audio':
                        if session.transcription and not session.transcription.done():
                            session.transcription.cancel()
                        session.transcription = session.task(session.handle_audio(base64.b64decode(msg['audio'], validate=True)))
                    elif kind == 'text':
                        if not isinstance(msg.get('text'), str) or not 0 < len(msg['text']) <= 500:
                            raise ValueError('text')
                        session.invalidate_input()
                        session.emit('transcript', text=msg['text'], provider='typed')
                        session.handle_text(msg['text'])
                    elif kind == 'speech_start':
                        session.invalidate_input()
                        session.paused = True
                        session.emit('listening')
                    elif kind == 'settings':
                        session.auto = bool(msg.get('auto', True))
                    elif kind == 'realtime_event':
                        event_kind = msg.get('event_kind')
                        if event_kind not in {'user_transcript', 'assistant_started', 'assistant_generated',
                                              'assistant_completed', 'assistant_cancelled',
                                              'assistant_failed', 'context_applied',
                                              'research_context_applied', 'assistant_audio_started',
                                              'assistant_audio_stopped',
                                              'speech_started', 'speech_stopped', 'error'}:
                            raise ValueError('realtime_event')
                        text = msg.get('text', '')
                        if not isinstance(text, str) or len(text) > 4000:
                            raise ValueError('realtime_text')
                        session.handle_realtime_event(
                            event_kind, text=text, response_id=msg.get('response_id'),
                            utterance_id=msg.get('utterance_id'), purpose=msg.get('purpose'))
                    elif kind == 'gps':
                        session.gps_update(msg)
                    elif kind == 'stop':
                        session.invalidate_input()
                        session.capture_manual_response()
                        session.handle_text('Zaustavi pričanje')
                    elif kind == 'resume':
                        session.manual_resume()
                    elif kind == 'playback':
                        unit = session.played.get(msg.get('utterance_id'))
                        state = msg.get('state')
                        if unit and state in {'started', 'completed', 'partial'}:
                            if state == 'completed' and unit['epoch'] == session.epoch:
                                unit['state'] = 'completed'
                                session.history.append({'role': 'assistant', 'text': unit['text']})
                            elif unit['state'] != 'completed':
                                unit['state'] = state
                            session.emit('playback', utterance_id=msg['utterance_id'],
                                         state=state, played_ms=msg.get('played_ms', 0))
                            session.maybe_deliver_research()
                    elif kind == 'ping':
                        session.emit('pong', client_ms=msg.get('client_ms'))
                except (ValueError, TypeError, KeyError):
                    session.emit('error', message='Poruka nije valjana.')
        except WebSocketDisconnect:
            pass
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            await session.close()
            archived_sessions.appendleft(session.monitor_snapshot())
            sessions.pop(sid, None)

    return router
