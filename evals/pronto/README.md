# PRONTO slice

The three labelled days of the **PRONTO heterogeneous benchmark** (Cranfield University's three-phase
flow facility), for checking how well Fuzer's change detection finds faults on a real rig.

| File | Day | Faults (onset, minutes in) |
| --- | --- | --- |
| `Testday2_merged.csv.gz` | 7 Sep 2017 | air blockage (6), air leakage (160) |
| `Testday3_merged.csv.gz` | 11 Sep 2017 | air leakage (15, 90), diverted flow (155, 285) |
| `Testday4_merged.csv.gz` | 12 Sep 2017 | slugging (59, 121; it starts the day slugging too) |

Each is the authors' own alignment of the day's DCS alarms and process data, labelled from the operation
log (`Pre-processed data/Aligned and labelled alarm and process data/` in the archive): one row a second,
12 alarm columns (0 or 1), 17 process readings (air and water flow in, pressures along the line and riser,
the separators' flows and levels, valve outputs) and a `Fault` label. The files carry no timestamps.
The raw process data, high-frequency pressure, ultrasonic data and other test days are not kept here.

`fuzer.evals.pronto` reads these and scores change detection against the labels (see
docs/timeseries.md). `fetch.py` downloads the same files again from Zenodo with range requests, without
the 1.7 GB zip.

## Source and licence

- Dataset: Stief, A., Tan, R., Cao, Y., Ottewill, J. R. *PRONTO
  heterogeneous benchmark dataset.* Zenodo, 2019. <https://doi.org/10.5281/zenodo.1341583>
- Paper: A. Stief, R. Tan, Y. Cao, J. R. Ottewill, N. F. Thornhill, J. Baranowski. *A heterogeneous
  benchmark dataset for data analytics: Multiphase flow facility case study.* Journal of Process Control,
  79 (2019) 41–55. <https://doi.org/10.1016/j.jprocont.2019.04.009>
- PRONTO received funding from the European Union's Horizon 2020 research and innovation programme under
  the Marie Skłodowska-Curie grant agreement No. 675215.

The data files in `data/` are © 2019 the authors and licensed under **Creative Commons Attribution 4.0
International** (`LICENSE`). Their contents are unchanged. Each CSV is gzip-compressed, and the folder
layout is flattened. This folder is left out of the Docker image (`.dockerignore`).
