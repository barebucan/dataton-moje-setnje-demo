# Moje šetnje — odabrana ruta i GPS vodič

https://moje-setnje-delta.vercel.app/

Verzija route-gps-v1 povezuje odabranu rutu planera s glasovnim vodičem. Gumb Pokreni vodiča prenosi točnu geometriju i identitete mjesta kroz sessionStorage iste stranice. GPX je u zatvorenim dodatnim opcijama.

Vodič koristi Geolocation.watchPosition i stvarne lokacijske callbackove preglednika. Nema automatskog simuliranja kretanja. Automatske priče čekaju svjež GPS do 50 m preciznosti i blizinu odabrane trase/mjesta. Izlazak s rute i loš signal jasno su označeni. Mikrofon i GPS traže se tek na pokretanje; završetak čisti oba i WebRTC vezu. AI koristi isti razgovor za priče i pitanja. Sesija se zatvara nakon 55 minuta i može se ponovno pokrenuti.

API određuje opise prema vlastitom katalogu javnih podataka i osam ranije pripremljenih priča; ne prihvaća izmišljene priče iz klijenta. Provjerava da su odabrana mjesta uz rutu. Potpisana kratkotrajna sesija veže AI kontekst uz ta mjesta. Odabrana geometrija rute šalje se Vercelu za pripremu vodiča. Nova očitanja GPS-a ostaju u pregledniku; AI dobiva javne opise i kontekst približenog mjesta, bez sirovih GPS koordinata. Točan položaj ipak se može zaključiti iz imena mjesta u kontekstu.

Neka mjesta imaju samo kratak javni opis; automatski vodič čita naziv i taj opis bez nagađanja. Za osam obrađenih mjesta zadržane su pripremljene priče. Grubi početni GPS dopušta pitanja, ali automatski opis čeka kvalitetniji signal. Karta prikazuje liniju puta; ovo nije zajamčena glasovna navigacija skretanje-po-skretanje. Preciznost i rad u pozadini ovise o uređaju/pregledniku.

AI API se naplaćuje zasebno; Vercel Hobby hosting ima besplatne kvote. Ključ je Secret samo na Vercelu, nije u repozitoriju. Ograničenje pokretanja je po instanci, nije tvrdi globalni limit računa.

Prošlo je 11 kontroliranih API provjera, 14 GPS/UI scenarija i 6 javnih funkcionalnih provjera sa stvarnim AI glasom. Odabir druge rute, GPX, priča, pitanje, izlazak s rute i zatvaranje sesije provjereni su na produkciji. GPS položaji bili su virtualni, mikrofon sintetički; nije obavljen terenski hod. Primjer planiranja trajao je 27,43 s; hladni izračun može potrajati dulje. Sažetak: PUBLIC_ROUTE_GPS_RELEASE.json; snimke: proof/route-gps-desktop.png i proof/route-gps-mobile.png.


## Aktualna javna UX dorada · 8. 11. 2026.

ux-v5 + conversation-v4 / route-gps-v1. https://moje-setnje-delta.vercel.app/

- Visible sticky mobile guide action, single primary mobile CTA
- Restore origin, time, preferences, every choice and selected route on return/reload without re-planning
- Short real route-specific places and green comparison in existing cards

Provjereno na javnoj produkciji na desktopu i širinama 360/390 px. Aktualna geometrija, izvorni financijski podaci i poslužiteljski izračuni ostaju isti. Detalji: UX_RELEASE_20261108.json. Izvor frontend dorade: 7f2fd29db63f9f6837cd1a9d251d3a6b97496a0c.
