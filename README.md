# Moje šetnje — odabrana ruta i GPS vodič

https://moje-setnje-delta.vercel.app/

Verzija route-gps-v1 povezuje odabranu rutu planera s glasovnim vodičem. Gumb Pokreni vodiča prenosi točnu geometriju i identitete mjesta kroz sessionStorage iste stranice. GPX je u zatvorenim dodatnim opcijama.

Vodič koristi Geolocation.watchPosition i stvarne lokacijske callbackove preglednika. Nema automatskog simuliranja kretanja. Automatske priče čekaju svjež GPS do 50 m preciznosti i blizinu odabrane trase/mjesta. Izlazak s rute i loš signal jasno su označeni. Mikrofon i GPS traže se tek na pokretanje; završetak čisti oba i WebRTC vezu. AI koristi isti razgovor za priče i pitanja. Sesija se zatvara nakon 55 minuta i može se ponovno pokrenuti.

API određuje opise prema vlastitom katalogu javnih podataka i osam ranije pripremljenih priča; ne prihvaća izmišljene priče iz klijenta. Provjerava da su odabrana mjesta uz rutu. Potpisana kratkotrajna sesija veže AI kontekst uz ta mjesta. GPS koordinate ostaju u pregledniku; AI dobiva javne opise i kontekst približenog mjesta, bez sirovih GPS koordinata. Točan položaj ipak se može zaključiti iz imena mjesta u kontekstu.

Neka mjesta imaju samo kratak javni opis; vodič ne smije izmišljati dodatne činjenice. Karta prikazuje liniju puta; ovo nije zajamčena glasovna navigacija skretanje-po-skretanje. Preciznost i rad u pozadini ovise o uređaju/pregledniku.

AI API se naplaćuje zasebno; Vercel Hobby hosting ima besplatne kvote. Ključ je Secret samo na Vercelu, nije u repozitoriju. Ograničenje pokretanja je po instanci, nije tvrdi globalni limit računa.

Kontrolirane API i pregledničke GPS provjere prošle su. Stvarna javna glasovna provjera nakon objave vodi se zasebno; kontrolirani rezultat nije terenska provjera hodanja.
