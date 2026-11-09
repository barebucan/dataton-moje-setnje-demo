"""Small, conversational location cues for model input; telemetry stays separate."""
import math


# Build a compact location brief from the coordinates and named street.
def location_brief(position=None, street=None):
    p = position if isinstance(position, dict) else {}
    street = p.get('street') or street
    parts = [f'Trenutačna lokacija: {street}.'] if street else []
    previous = p.get('story_target') or {}
    if previous.get('state') == 'left' and previous.get('name'):
        parts.append(f"Napustio si: {previous['name']}. Ne dodaj nove detalje o tom mjestu.")
    place = p.get('poi_name') or p.get('nearest_landmark')
    meters = p.get('distance_m')
    valid_distance = (isinstance(meters, (int, float)) and not isinstance(meters, bool)
                      and math.isfinite(meters) and meters >= 0)
    if place:
        gap = f', oko {round(meters)} m od tebe' if valid_distance else ''
        parts.append(f'U blizini je: {place}{gap}.')
    if p.get('movement') == 'approaching':
        parts.append('Približavaš se tom mjestu.')
    elif p.get('movement') == 'leaving':
        parts.append('Udaljavaš se od tog mjesta.')
    return ' '.join(parts)


def conversation_brief(messages):
    roles = {'user': 'Korisnik', 'assistant': 'Vodič', 'system': 'Sustav'}
    return '\n'.join(f"{roles.get(item.get('role'), 'Poruka')}: {item['text']}"
                     for item in messages or [] if isinstance(item, dict) and item.get('text'))
