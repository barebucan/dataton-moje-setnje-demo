/* Read-only human-readable view of the actual conversation/research event stream. */
(() => {
  'use strict';
  const app = {sessions: [], selected: null, ready: false};
  const movementLabels = {approaching: 'Približavaš se', leaving: 'Udaljavaš se',
    steady: 'Položaj je stabilan', unknown: 'Prvi signal položaja'};
  const purposeLabels = {story: 'Priča', story_resume: 'Nastavak priče', answer: 'Odgovor',
    answer_resume: 'Nastavak odgovora', answer_with_resume_offer: 'Odgovor i ponuda nastavka',
    research_ack: 'Potvrda pitanja', research_answer: 'Odgovor nakon istraživanja',
    story_continuation: 'Nastavak priče', continuation_offer: 'Ponuda nastavka'};
  const clock = value => {
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat('hr-HR', {
      timeZone: 'Europe/Zagreb', hour: '2-digit', minute: '2-digit', second: '2-digit',
      fractionalSecondDigits: 3, hour12: false}).format(date) : '—';
  };
  const number = value => Number.isFinite(Number(value)) ? new Intl.NumberFormat('hr-HR', {
    maximumFractionDigits: 1}).format(Number(value)) : '—';
  const safeObject = text => {
    if (typeof text !== 'string') return null;
    try { const parsed = JSON.parse(text); return parsed && typeof parsed === 'object' ? parsed : null; }
    catch { return null; }
  };
  function positionOf(event) {
    const p = event.position_context || event.position;
    if (p && !Array.isArray(p)) return p;
    if (event.kind === 'realtime_context_applied') return safeObject(event.text);
    if (event.street || event.poi_name) return {...event,
      coordinates: Array.isArray(event.position) ? event.position : event.coordinates};
    return null;
  }
  function positionText(p) {
    if (!p) return '';
    const place = p.poi_name || p.nearest_landmark || '';
    let text = p.street ? 'Nalaziš se: ' + p.street + '.' : 'Primljen je novi položaj.';
    if (place) text += ' Najbliže mjesto je ' + place
      + (p.distance_m != null ? ', udaljeno ' + number(p.distance_m) + ' m.' : '.');
    if (p.movement && p.movement !== 'unknown') text += ' ' + (movementLabels[p.movement] || 'Položaj je ažuriran') + '.';
    return text;
  }
  function readableObject(value) {
    if (!value || typeof value !== 'object') return String(value ?? '');
    if (value.street || value.coordinates || value.poi_name) return positionText(value);
    if (Array.isArray(value)) return value.map(readableObject).filter(Boolean).join('\n');
    const names = {text: 'Sadržaj', story: 'Priča', question: 'Pitanje', title: 'Naslov',
      name: 'Mjesto', poi_name: 'Mjesto', look_for: 'Pogledaj', role: 'Uloga',
      instructions: 'Uputa', facts: 'Činjenice', sources: 'Izvori', card: 'Kartica',
      remaining_text: 'Još neizgovoreni dio', heard_text: 'Zabilježeni dio razgovora',
      position_context: 'Položaj', position: 'Položaj', distance_m: 'Udaljenost u metrima',
      movement: 'Kretanje', url: 'Poveznica', source_url: 'Poveznica'};
    return Object.entries(value).filter(([key]) => names[key]).map(([key, item]) =>
      names[key] + ': ' + readableObject(item)).join('\n') || 'Kontekst je ažuriran.';
  }
  function humanText(raw) {
    let text = String(raw || '');
    // Turn embedded structured context into prose, including inside full system prompts.
    let result = '', cursor = 0;
    while (cursor < text.length) {
      const start = text.indexOf('{', cursor);
      if (start < 0) { result += text.slice(cursor); break; }
      result += text.slice(cursor, start);
      let depth = 0, quoted = false, escaped = false, end = start;
      for (; end < text.length; end++) {
        const char = text[end];
        if (quoted) {
          if (escaped) escaped = false;
          else if (char === '\\') escaped = true;
          else if (char === '"') quoted = false;
        } else if (char === '"') quoted = true;
        else if (char === '{') depth++;
        else if (char === '}' && --depth === 0) { end++; break; }
      }
      const parsed = safeObject(text.slice(start, end));
      result += parsed ? readableObject(parsed) : text.slice(start, end);
      cursor = end > start ? end : start + 1;
    }
    return result.replace(/\[([^\]]+)\]\(https?:\/\/[^)]+\)/g, '$1')
      .replace(/[^]*/g, '').trim();
  }
  function message(event, role, title, body, suffix = '') {
    return {key: String(event.seq ?? event._index) + suffix, at: event.at, role, title,
      body: humanText(body), status: '', details: '', cue: '', sources: [], position: null};
  }
  const notices = {
    story_interrupted_by_user: ['Prekinuo si priču pitanjem.', 'Vodič prvo odgovara na tvoje pitanje.'],
    resume_offered: ['Želiš li nastavak?', 'Vodič je ponudio nastavak i čeka tvoj odgovor.'],
    resume_accepted: ['Nastavak je prihvaćen.', 'Nastavlja se samo neizgovoreni dio.'],
    resume_declined: ['Nastavak je odbijen.', 'Vodič ostaje tih.'],
    resume_changed_topic: ['Promijenjena je tema.', ''],
    resume_topic_cleared: ['Prethodna tema je zatvorena.', ''],
    resume_waiting: ['Nastavak je na čekanju.', 'Najprije se zaustavlja prethodni odgovor.'],
    response_paused: ['Zaustavio si govor.', ''],
    response_pause_ready: ['Zaustavljeni govor spreman je za nastavak.', ''],
    stopped: ['Govor je zaustavljen.', 'Položaj se i dalje prati.'],
    resume: ['Govor može nastaviti.', ''],
    story_transition_queued: ['Priprema se sljedeća priča.', ''],
    story_transitioned: ['Odabrana je sljedeća tema.', ''],
    landmark_entered: ['Ušao si u blizinu znamenitosti.', ''],
    landmark_left: ['Napustio si znamenitost.', ''],
    story_interrupted: ['Prethodna priča je zaustavljena.', ''],
    continuation_offered: ['Ponuđen je nastavak priče.', 'Vodič čeka tvoju odluku.'],
  };
  function sourcesOf(card) {
    return (card?.sources || []).flatMap(source => {
      const value = typeof source === 'string' ? {url: source} : source;
      try {
        const url = new URL(value.url);
        return ['https:', 'http:'].includes(url.protocol) ?
          [{url: url.href, title: value.title || url.hostname.replace(/^www\./, '')}] : [];
      } catch { return []; }
    });
  }
  function buildView(input) {
    const events = (input || []).map((event, index) => ({...event, _index: index}));
    const conversation = [], research = [], movement = [];
    const promptById = new Map(), responseToUtterance = new Map(), researchById = new Map();
    const contextBySample = new Map(), generated = new Map(), handoffs = new Map(), researchRequests = new Map();
    const assistantRows = new Map();
    for (const e of events) {
      if (e.kind === 'realtime_prompt' && e.utterance_id) promptById.set(e.utterance_id, e);
      if (e.response_id && e.utterance_id) responseToUtterance.set(e.response_id, e.utterance_id);
      if (e.request_id && e.card) researchById.set(e.request_id, e);
      if (e.kind === 'realtime_context' && e.instructions) contextBySample.set(e.position?.sample_seq, e);
      if (e.kind === 'realtime_assistant_generated') generated.set(e.utterance_id || e.response_id, e);
    }
    function addLocation(e, p, details) {
      const last = conversation.at(-1), key = (p.street || '') + ':' + (p.poi_id || p.poi_name || '');
      if (last?.locationKey === key) {
        last.body = positionText(p); last.position = p; last.at = e.at;
        last.updates = (last.updates || 1) + 1;
        if (details) last.details = humanText(details);
        last.status = last.updates + ' ažuriranja položaja';
        return;
      }
      const row = message(e, 'system', 'Tvoj položaj → GPT', positionText(p));
      row.position = p; row.locationKey = key; row.details = humanText(details);
      row.status = 'Poslano vodiču'; conversation.push(row);
    }
    function getAssistant(e) {
      const id = e.utterance_id || responseToUtterance.get(e.response_id) || e.response_id || 'orphan-' + e._index;
      let row = assistantRows.get(id);
      if (!row) {
        const prompt = promptById.get(id), purpose = e.purpose || prompt?.purpose;
        row = message(e, 'assistant', (purposeLabels[purpose] || 'Odgovor')
          + (prompt?.poi_name ? ' · ' + prompt.poi_name : ''), '');
        row.key = 'assistant:' + id; row.status = 'Priprema odgovor';
        row.body = 'Prijepis odgovora još nije dostupan.';
        assistantRows.set(id, row); conversation.push(row);
      }
      return row;
    }
    for (const e of events) {
      const kind = e.kind;
      if (kind === 'realtime_user_transcript' || kind === 'transcript') {
        const previous = conversation.at(-1);
        if (!(previous?.role === 'user' && previous.body === e.text &&
          Math.abs(new Date(previous.at) - new Date(e.at)) < 1500)) {
          conversation.push(message(e, 'user', '', e.text));
        }
      } else if (kind === 'realtime_context_applied') {
        const p = positionOf(e);
        if (p) addLocation(e, p, contextBySample.get(p.sample_seq)?.instructions);
      } else if (kind === 'realtime_session_update' || kind === 'session_update') {
        const row = message(e, 'system', 'Ažuriran kontekst razgovora',
          e.instructions?.includes('Prekinuta tema koju možeš nastaviti') ?
            'Zabilježena je prekinuta tema i dio priče koji se može nastaviti uz tvoju odluku.' :
            'Ažurirane su upute koje vodič koristi u nastavku razgovora.');
        row.details = humanText(e.instructions); conversation.push(row);
      } else if (kind === 'realtime_prompt') {
        const row = message(e, 'system', 'Uputa GPT-u'
          + (e.poi_name ? ' · ' + e.poi_name : ''),
          e.purpose === 'research_ack' ? 'Kratko potvrdi pitanje dok istraživač traži odgovor.' :
          e.purpose === 'answer_with_resume_offer' ? 'Odgovori na pitanje, zatim jednom ponudi nastavak prekinute priče.' :
          e.purpose === 'research_answer' ? 'Odgovori koristeći provjereni rezultat istraživanja.' :
          e.purpose === 'story' || e.purpose === 'story_resume' ?
            'Pričaj o ovom mjestu koristeći pripremljeni materijal i dosadašnji razgovor.' :
            'Odgovori na posljednju korisnikovu poruku uz aktualni položaj i dosadašnji razgovor.');
        row.details = humanText(e.instructions); row.cue = humanText(e.text || e.cue_card?.text || e.cue_card?.story);
        row.status = 'Poslana uputa'; conversation.push(row);
      } else if (kind === 'realtime_assistant_started' || kind === 'realtime_assistant_generated' ||
          kind === 'realtime_assistant_audio_started' || kind === 'realtime_assistant_audio_stopped' ||
          kind === 'realtime_assistant_completed' || kind === 'realtime_assistant_cancelled' ||
          kind === 'realtime_assistant_failed') {
        const row = getAssistant(e);
        if (e.text) row.body = humanText(e.text);
        if (kind === 'realtime_assistant_generated') {
          row.prepared = true;
          if (!row.playing && !row.terminal) row.status = 'Pripremljeno · čeka govor';
        } else if (kind === 'realtime_assistant_audio_started') {
          row.playing = true; row.status = 'Govori';
        } else if (kind === 'realtime_assistant_audio_stopped') {
          row.playing = false; if (!row.terminal) row.status = 'Govor završen';
        } else if (kind === 'realtime_assistant_completed') {
          row.terminal = true; row.status = 'Odgovor dovršen';
        } else if (kind === 'realtime_assistant_cancelled') {
          row.terminal = true; row.playing = false; row.status = 'Prekinuto';
          if (!e.text) {
            const pending = generated.get(e.utterance_id || e.response_id);
            row.body = pending?.text ? humanText(pending.text) : 'Govor je prekinut; odslušani dio nije zabilježen.';
            if (pending?.text) row.note = 'Pripremljeni tekst prije prekida. Nije potvrđeno da je sve izgovoreno.';
          }
        } else if (kind === 'realtime_assistant_failed') {
          row.terminal = true; row.status = 'Odgovor nije uspio'; row.error = true;
          row.body = 'Glasovni odgovor nije dovršen.';
        }
      } else if (notices[kind]) {
        const [title, defaultBody] = notices[kind];
        let body = defaultBody;
        const place = e.poi_name || e.from_poi_name || e.landmark?.name || '';
        if (kind === 'landmark_left') body = (place ? 'Napustio si ' + place + '. ' : '')
          + 'Stara tema više nije vezana uz ono što je pred tobom.';
        else if (kind === 'landmark_entered') body = (place ? 'Sada si u blizini: ' + place + '. ' : '')
          + (e.distance_m != null ? 'Udaljenost je ' + number(e.distance_m) + ' m.' : '');
        else if (kind.startsWith('story_transition')) body = 'Sljedeće mjesto: ' + (e.to_poi_name || 'nova znamenitost')
          + '. Ovo je priprema teme, a ne potvrda da je govor već počeo.';
        else if (kind === 'resume_changed_topic') body = e.text || defaultBody;
        const row = message(e, 'system', title, body);
        row.author = 'Sustav'; row.status = 'Događaj šetnje'; conversation.push(row);
        if (kind === 'landmark_entered' || kind === 'landmark_left') movement.push({...row});
      } else if (kind === 'cue_card' || kind === 'cue_card_ready' || kind === 'realtime_cue_card') {
        const row = message(e, 'system', 'Pripremljena kartica'
          + (e.poi_name ? ' · ' + e.poi_name : ''), 'Materijal za sljedeći dio razgovora.');
        row.cue = humanText(e.card?.text || e.card?.story || e.text);
        row.details = humanText(e.instructions); conversation.push(row);
      } else if (kind === 'error' || kind === 'realtime_error') {
        const row = message(e, 'system', 'Veza je prijavila pogrešku', e.message || e.text);
        row.error = true; conversation.push(row);
      } else if (kind === 'transcript_discarded') {
        conversation.push(message(e, 'system', 'Prijepis nije uključen u razgovor',
          'Prepoznat je artefakt prijepisa pa nije poslan vodiču.'));
      } else if (kind === 'session' || kind === 'realtime_connected') {
        conversation.push(message(e, 'system', kind === 'session' ? 'Šetnja je otvorena' : 'Glasovni razgovor je povezan',
          kind === 'session' ? 'Sustav priprema razgovor i prati položaj.' : 'Možeš razgovarati s vodičem.'));
      }
      if (kind === 'research_started') {
        const row = message(e, 'system', e.explicit ? 'Tvoje pitanje → istraživač' : 'Zadatak istraživaču', e.question);
        row.author = 'Sustav → istraživač'; row.positionInBody = true;
        row.status = 'Pretraživanje u tijeku'; row.position = positionOf(e); research.push(row);
        researchRequests.set(e.request_id || e.poi_id, row);
      } else if (kind === 'research_ready') {
        const request = researchRequests.get(e.request_id || e.poi_id);
        if (request) request.status = 'Pretraga završena';
        const row = message(e, 'research', 'Istraživač je pronašao', e.card?.text || e.question);
        row.sources = sourcesOf(e.card);
        const counts = e.card?.search_call_count, duration = e.card?.elapsed_ms;
        row.status = [counts != null ? counts + (counts === 1 ? ' pretraga' : ' pretrage') : '',
          duration != null ? number(duration / 1000) + ' s' : ''].filter(Boolean).join(' · ');
        research.push(row);
      } else if (kind === 'realtime_research_result') {
        const row = message(e, 'handoff', 'Kartica za glavnog GPT-a', e.card?.text);
        row.key = 'handoff:' + (e.request_id || e._index); row.status = 'Pripremljeno za slanje';
        row.sources = sourcesOf(e.card); row.position = positionOf(e);
        handoffs.set(e.request_id, row); research.push(row);
      } else if (kind === 'realtime_research_context_applied') {
        const id = e.request_id || e.text, lookup = researchById.get(id);
        let row = handoffs.get(id);
        if (!row) {
          row = message(e, 'handoff', 'Poslano glavnom GPT-u', lookup?.card?.text || 'Rezultat pretrage dodan je u razgovor vodiča.');
          row.key = 'handoff:' + id; row.sources = sourcesOf(lookup?.card); research.push(row); handoffs.set(id, row);
        }
        row.title = 'Poslano glavnom GPT-u'; row.status = 'Dodano u isti razgovor'; row.at = e.at;
        const received = message(e, 'system', 'Novi kontekst iz istraživanja',
          'Istraživački loop poslao je provjeren sadržaj za odgovor vodiča.');
        received.cue = humanText(lookup?.card?.text); received.sources = sourcesOf(lookup?.card);
        conversation.push(received);
      } else if (kind === 'research_failed' || kind === 'research_discarded') {
        const request = researchRequests.get(e.request_id || e.poi_id);
        if (request) request.status = kind === 'research_failed' ? 'Bez rezultata' : 'Zastarjeli upit';
        const row = message(e, 'research', kind === 'research_failed' ? 'Pretraga bez upotrebljivog rezultata' : 'Stari rezultat je preskočen',
          e.question || 'Nema novog sadržaja za vodiča.');
        row.status = kind === 'research_failed' ? 'Nije poslano vodiču' : 'Noviji upit ima prednost';
        research.push(row);
      }
      if (kind === 'gps_processed') {
        const p = positionOf(e), last = movement.at(-1);
        const locationKey = (p?.street || '') + ':' + (p?.poi_id || '');
        if (last?.locationKey === locationKey && new Date(e.at) - new Date(last.startedAt || last.at) < 15000) {
          last.at = e.at; last.body = positionText(p); last.position = p;
          last.status = (last.updates = (last.updates || 1) + 1) + ' uzoraka položaja';
        } else {
          const row = message(e, 'system', 'Položaj u šetnji', positionText(p));
          row.position = p; row.locationKey = locationKey; row.startedAt = e.at; movement.push(row);
        }
      }
    }
    return {conversation: conversation.slice(-160), research: research.slice(-120), movement: movement.slice(-70)};
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {buildView, humanText, positionText, sourcesOf}; return;
  }
  const $ = selector => document.querySelector(selector);
  window.laganoMonitor = Object.assign(app, {buildView});
  const roles = {user: ['Ti', 'TI'], assistant: ['GPT vodič', 'GPT'], system: ['Sustav → GPT', 'S'],
    research: ['Istraživač', 'AI'], handoff: ['Istraživač → vodič', '→']};
  function node(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text != null) result.textContent = text;
    return result;
  }
  function paragraphs(parent, text, className) {
    for (const paragraph of String(text || '').split(/\n{2,}/).filter(Boolean))
      parent.append(node('p', className, paragraph));
  }
  function renderPosition(parent, p) {
    if (!p) return;
    const info = node('div', 'position-meta');
    const coords = p.coordinates || (p.lon != null && p.lat != null ? [p.lon, p.lat] : null);
    if (coords?.length === 2 && coords.every(value => Number.isFinite(Number(value))))
      info.append(node('span', '', 'Položaj: ' + Number(coords[1]).toFixed(5) + ', ' + Number(coords[0]).toFixed(5)));
    if (p.speed_mps != null) info.append(node('span', '', 'Brzina: ' + number(p.speed_mps) + ' m/s'));
    if (p.accuracy_m != null) info.append(node('span', '', 'Točnost: ' + number(p.accuracy_m) + ' m'));
    if (info.childElementCount) parent.append(info);
  }
  function renderMessage(row) {
    const item = node('li', 'message-row role-' + row.role + (row.error ? ' is-error' : ''));
    item.dataset.key = row.key; item.dataset.role = row.role;
    const [name, avatar] = roles[row.role] || roles.system;
    item.append(node('span', 'message-avatar', avatar));
    const content = node('div', 'message-content'), meta = node('div', 'message-meta');
    meta.append(node('strong', '', row.author || name));
    if (row.status) meta.append(node('span', 'message-status', row.status));
    const time = node('time', '', clock(row.at)); time.dateTime = row.at || ''; meta.append(time);
    const bubble = node('div', 'message-bubble');
    if (row.title) bubble.append(node('h3', '', row.title));
    paragraphs(bubble, row.body);
    if (row.note) paragraphs(bubble, row.note, 'minor');
    if (row.position && (row.role !== 'system' || row.positionInBody)) paragraphs(bubble, positionText(row.position), 'minor');
    renderPosition(bubble, row.position);
    if (row.cue) {
      const cue = node('div', 'cue-card'); cue.append(node('strong', '', 'Kartica za priču · pripremljeni materijal'));
      paragraphs(cue, row.cue); bubble.append(cue);
    }
    if (row.sources?.length) {
      const links = node('div', 'message-sources');
      for (const source of row.sources) {
        const link = node('a', '', source.title); link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; links.append(link);
      }
      bubble.append(links);
    }
    if (row.details) {
      const details = node('details', 'message-details');
      details.append(node('summary', '', 'Cijela uputa koju je sustav poslao'));
      paragraphs(details, row.details); bubble.append(details);
    }
    content.append(meta, bubble); item.append(content);
    return item;
  }
  function renderMessages(selector, rows, followButton, changedSession) {
    const list = $(selector), button = $(followButton);
    const follow = changedSession || !list.children.length || list.scrollHeight - list.scrollTop - list.clientHeight < 65;
    const savedTop = list.scrollTop;
    const previous = new Map([...list.children].map(child => [child.dataset.key, child]));
    const keys = new Set(rows.map(row => row.key));
    for (const child of [...list.children]) if (!keys.has(child.dataset.key)) child.remove();
    if (!rows.length) {
      if (!list.children.length) list.append(node('li', 'message-empty', selector.includes('research') ?
        'Ovdje će se pojaviti zadatak i rezultati istraživača.' : 'Čekam prvu poruku u razgovoru.'));
      button.hidden = true; return;
    }
    let cursor = list.firstElementChild;
    for (const row of rows) {
      const signature = JSON.stringify(row), old = previous.get(row.key);
      let item = old;
      if (!old || old._signature !== signature) {
        item = renderMessage(row); item._signature = signature;
        if (old?.querySelector('details')?.open && item.querySelector('details')) item.querySelector('details').open = true;
        if (old?.isConnected) { if (cursor === old) cursor = old.nextElementSibling; old.replaceWith(item); }
      }
      if (cursor === item) cursor = item.nextElementSibling;
      else list.insertBefore(item, cursor);
    }
    if (follow) list.scrollTop = list.scrollHeight;
    else list.scrollTop = savedTop;
    button.hidden = follow;
  }
  function renderMovement(rows) {
    const list = $('#movement-events');
    const signature = JSON.stringify(rows);
    if (list._signature === signature) return;
    const top = list.scrollTop; list.replaceChildren(); list._signature = signature;
    for (const row of rows) {
      const item = node('li', 'movement-event'), time = node('time', '', clock(row.at));
      time.dateTime = row.at || '';
      const body = node('div'); body.append(node('p', '', row.body || row.title));
      if (row.status) body.append(node('small', '', row.status));
      item.append(time, body); list.append(item);
    }
    if (!rows.length) list.append(node('li', 'message-empty', 'Još nema signala položaja.'));
    list.scrollTop = top;
  }
  function render() {
    const select = $('#session'), prior = app.renderedSession;
    if (!app.sessions.some(session => session.id === app.selected))
      app.selected = app.sessions.find(session => !session.closed)?.id || app.sessions[0]?.id || null;
    const optionsSignature = app.sessions.map(session => session.id + ':' + session.closed).join('|');
    if (select._signature !== optionsSignature) {
      select.replaceChildren();
      for (const session of app.sessions) {
        const option = node('option', '', (session.closed ? 'Završena šetnja' : 'Aktivna šetnja') + ' · ' + clock(session.created_at));
        option.value = session.id; select.append(option);
      }
      if (!app.sessions.length) { const option = node('option', '', 'Nema aktivne sesije'); option.value = ''; select.append(option); }
      select._signature = optionsSignature;
    }
    select.value = app.selected || '';
    const session = app.sessions.find(row => row.id === app.selected);
    $('#empty').hidden = Boolean(session); $('#dashboard').hidden = !session;
    $('#connection').classList.toggle('online', Boolean(session && !session.closed));
    $('#connection-text').textContent = session ? (session.closed ? 'Spremljeni tijek završene šetnje.' : 'Razgovor uživo.') : 'Čekam šetnju…';
    app.renderedSession = app.selected;
    if (!session) {
      for (const id of ['landmark', 'distance', 'movement']) $('#' + id).textContent = '—';
      $('#story-count').textContent = '0';
      return;
    }
    const latest = session.latest_gps || {}, events = session.events || [];
    $('#landmark').textContent = [latest.street, latest.poi_name || session.active_landmark?.name].filter(Boolean).join(' · ') || '—';
    $('#distance').textContent = latest.distance_m == null ? '—' : number(latest.distance_m) + ' m';
    $('#movement').textContent = movementLabels[latest.movement] || '—';
    $('#story-count').textContent = events.filter(event => event.kind === 'story_triggered').length;
    const speaking = session.conversation_running || session.transcription_running;
    $('#conversation-state').textContent = session.closed ? 'Završeno' : speaking ? 'Razgovara' : 'Sluša';
    $('#conversation-state').classList.toggle('busy', Boolean(speaking));
    $('#research-state').textContent = session.research_running ? 'Pretražuje' : 'Čeka upit';
    $('#research-state').classList.toggle('busy', Boolean(session.research_running));
    const rows = buildView(events);
    renderMessages('#conversation-events', rows.conversation, '#conversation-latest', prior !== app.selected);
    renderMessages('#research-events', rows.research, '#research-latest', prior !== app.selected);
    renderMovement(rows.movement);
  }
  $('#session').onchange = event => { app.selected = event.target.value; render(); };
  for (const name of ['conversation', 'research']) {
    const list = $('#' + name + '-events'), button = $('#' + name + '-latest');
    button.onclick = () => { list.scrollTop = list.scrollHeight; button.hidden = true; };
    list.addEventListener('scroll', () => { if (list.scrollHeight - list.scrollTop - list.clientHeight < 65) button.hidden = true; });
  }
  async function poll() {
    try {
      const response = await fetch('/voice/monitor/state', {cache: 'no-store'});
      if (!response.ok) throw Error('monitor');
      const data = await response.json(); app.sessions = data.sessions || []; render(); app.ready = true;
    } catch {
      $('#connection').classList.remove('online'); $('#connection-text').textContent = 'Veza je prekinuta. Pokušavam ponovno…';
    }
    setTimeout(poll, 900);
  }
  poll();
})();
