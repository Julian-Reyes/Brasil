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
