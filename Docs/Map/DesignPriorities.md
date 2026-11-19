# Design priorities

## Hero area 1: the beach from Leblon to Arpoador

The continuous beachfront from Leblon to Arpoador is one of the most iconic and important parts of the game.
It gets the highest fidelity, is built and verified first, and is protected from compression.

**Real geography (to verify against the full-area reference map):**

* About 4 km of continuous beach facing south onto the Atlantic: Praia do Leblon (west of the Jardim de Alah
  canal), then Praia de Ipanema, ending at Praia do Arpoador and the Pedra do Arpoador headland.
* Beachfront boulevard: Av. Delfim Moreira (Leblon), then Av. Vieira Souto (Ipanema), with the beach promenade
  and cycle path alongside.
* Parallel inland spines: Av. Ataulfo de Paiva / R. Visconde de Piraja (commercial), R. Prudente de Morais,
  Av. Epitacio Pessoa around Lagoa Rodrigo de Freitas behind.
* Anchors: Morro Dois Irmaos and Pedra da Gavea closing the western view; the Jardim de Alah canal break;
  Pedra do Arpoador at the east end, with the link to Copacabana (R. Francisco Otaviano). Lifeguard posts
  (Postos 7-12) structure the beach.

**Rules for the compressed map (Phase 3):**

1. Keep the beachfront boulevard close to real length (compress at most ~10-15%) and real shape: the long,
   gentle south-facing arc, unbroken except at Jardim de Alah.
2. Preserve the sightlines: Dois Irmaos from anywhere along the beach, Pedra do Arpoador from Ipanema.
3. Compress *behind* the beach instead: fewer repetitive blocks between the beach and Lagoa. Keep the
   beachfront, the main commercial spine and the Lagoa edge; drop or merge intermediate residential streets.
4. Keep real-scale building heights and street widths along the front; this skyline is the signature image.
5. Keep the direct connections: Av. Niemeyer at the west end (to Vidigal), Arpoador to Copacabana at the east
   end, and the Lagoa-side routes north to the tunnels.

**Next build step:** a 1:1 source test area covering Leblon, Ipanema and Arpoador (beach to Lagoa), using the
same pipeline as the Vidigal test.

## Starting area: Vidigal (first edition of the compressed map)

Decided 2026-11-18. The player starts in Vidigal as a low-level criminal: his house, neighbourhood and crew are
here. Progression goes from the favela to owning high-rises in the rich parts of town (Leblon / Ipanema / Lagoa),
which are in plain view from Vidigal: the aspiration is visible from home. We do not build all of Vidigal, just
enough for the game to feel good. Reference for the structure (not the content): how GTA V's Los Angeles map
pairs a compact starting neighbourhood with visible, aspirational districts.

**Central hub (idealised, designed for gameplay):**

* the player's house;
* the classic favela soccer field (campinho), on a terraced platform with retaining walls and a high fence;
* a small praca with places to buy things (padaria, boteco / bar, mercadinho, barber, car workshop);
* mototaxi stand at the hub entrance; crew hangout;
* reachable by car on Av. Pres. Joao Goulart; compact and readable, everything a short walk apart.

**Where (first blockout, 2026-11-18):** lower Vidigal, inside the last hairpin of Av. Pres. Joao Goulart before
Av. Niemeyer (~40 m up): the flattest field-sized ground next to the avenue, at a real commercial core (Teatro do
Vidigal / Nos do Morro, schools, Pizzaria 3 Irmaos, Pastel do Ceu, Bar Lacubaco), 80 m from the real mototaxi
stand at the Niemeyer entrance, so home is a quick drive from the city. Shops line the avenue's upper leg, the
campinho sits in the hairpin a few metres below street level, a small praca in the bend, the player's house
beside the field, the crew hangout on the bend's tip looking at the sea. First site tried and rejected: the real
civic core higher up (~100 m, Associacao dos Moradores / clinic), 47% cross-slope. Layout is config
(`places.hubs`), to be moved after the walk-around test.

**Real places marked in the realistic zone:** Vila Olimpica Adilio (big soccer field, top), Quadra do Alto,
Bar da Laje (rooftop bar with the view), Anfitriao theatre, Teatro do Vidigal, Igreja Metodista, UPP police
base, Associacao dos Moradores, clinic, Mototaxi Cooper Zona Sul, the Av. Niemeyer motels, the Sheraton, the
Dois Irmaos trail to the summits.

**Hub placement rule (user, 2026-11-18):** the hub is where the regular, non-rich residents live, in the lower /
middle favela. **Not at the top:** Bar da Laje / the top of Vidigal is a famous tourist and wealthier spot, so it
belongs to the realistic zone as a destination, never the starting hub. "Idealised" here means stylised: a
designed, curated version of the real place.

**Praca in the hub (user, 2026-11-18):** the hub should be built around a proper praca (public square: paved,
trees, benches, maybe a kiosk), the social heart, next to the campinho, with the shops and boteco around it.
The current blockout's praca (18 x 12 m tucked in a road bend) is too small and off to the side.

**Outside the hub: realistic Vidigal.** Dense informal housing on the real street / beco / stair network
(procedural infill where OSM is under-mapped), and Vidigal's real iconic places where they exist.

**Rules for the compressed map:**

1. Keep the Av. Niemeyer entrance at the bottom and the switchback climb of Av. Pres. Joao Goulart; the hub sits
   on it.
2. Keep the sightlines: Leblon / Ipanema towers and the beach from the hub and the top; Dois Irmaos above.
3. Keep Dois Irmaos at its real height (surveyed 539 m; landmark-summit correction) and the trail to the top.
4. Compress the realistic outer favela, not the hub.
