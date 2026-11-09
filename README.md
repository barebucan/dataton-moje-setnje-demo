# Moje šetnje

Tim DELTA · Datathon Portala otvorenih podataka · Izazov A — Društvo i javne usluge

Moje šetnje pomažu građanima i posjetiteljima Zagreba pronaći pješačku rutu prema polazištu, trajanju i interesima. Glasovni vodič prati šetnju i odgovara na pitanja o mjestima uz trasu.

## Isprobajte

- [Planer šetnji](https://moje-setnje-delta.vercel.app/app)
- [Glasovni vodič na Zelenoj potkovi](https://moje-setnje-delta.vercel.app/voice?demo=green-horseshoe)
- [Paket s videom i uputama](https://github.com/barebucan/dataton-moje-setnje-demo/releases/tag/predaja)

Odaberite polazište, trajanje i interes. Za razgovor pokrenite demonstracijsku rutu i dopustite mikrofon. Javni demo koristi poslužiteljsku konfiguraciju projekta.

## Struktura

| Mapa | Sadržaj |
|---|---|
| [wordpress](wordpress) | Karta, planer i odabir šetnje |
| [backend](backend) | Planiranje ruta i usluga glasovnog vodiča |
| [voice-ui](voice-ui) | Glasovno sučelje i prikaz izvora odgovora |
| [data](data) | Ulazni podaci, obrađeni slojevi i trag podrijetla |
| [scripts](scripts) | Lokalna instalacija i upravljanje servisima |
| vendor | Ovisnosti potrebne za lokalnu instalaciju |

WordPress prikazuje kartu i planer. FastAPI koordinira izračun pješačkih ruta u GraphHopperu i povezuje gradske lokacije s trasom. WebRTC prenosi razgovor, a istraživačka usluga dopunjuje odgovore javnim izvorima.

## Lokalno pokretanje

Na Linuxu x86-64 ili WSL2 potrebni su Python 3.12+, Java 17+ i internetska veza. U korijenu repozitorija ili u mapi `DEMO` iz predajnog paketa pokrenite `python3 scripts/bootstrap.py`, a zatim otvorite `http://127.0.0.1:18319/`. Za lokalni glas unesite vlastiti ključ u `private/voice.env` prema predlošku `backend/voice.env.example`. Za gašenje pokrenite `python3 scripts/manage.py stop`.

[Izvori i licence](ATTRIBUTION.md) · [Podrijetlo gradskih podataka](data/provenance/portal-manifest.json)
