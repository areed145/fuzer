# 3W slice

A small slice of the **3W Dataset** (Petrobras), for scoring how well Fuzer finds real well upsets.
Each file is one instance: a few hours of one offshore well's readings at 1 Hz, with a label for
every second (0 normal, n an upset, 100 + n its transient).

| File | Event | Onset |
| --- | --- | --- |
| `c0_WELL-00002_20131214150127` | normal operation | |
| `c0_WELL-00003_20170820220053` | normal operation | |
| `c1_WELL-00002_20140126180030` | abrupt increase of BSW (water cut) | 139 min in |
| `c2_WELL-00003_20180206171508` | spurious closure of the DHSV | 84 min in |
| `c2_WELL-00011_20140726165720` | spurious closure of the DHSV | 78 min in |
| `c5_WELL-00016_20180426131710` | rapid productivity loss | 68 min in |
| `c6_WELL-00002_20140212160333` | quick restriction in the production choke | 310 min in |
| `c6_WELL-00002_20140325160304` | quick restriction in the production choke | 153 min in |

All are real instances (not 3W's simulated or hand-drawn ones), chosen because each has the tree
pressure, tree temperature and annulus pressure, and a normal stretch before its onset. Severe slugging
and flow instability are left out (their records are the upset from the start), and so are the
hydrates (3 to 6 MB each). Some readings are frozen in the source: WELL-00002's annulus pressure holds
one value for hours at a time. The eval counts those as frozen readings to find, not as noise.

`fuzer.evals.wells3w` replays these onto a fake Okan well and scores change detection and instrument
health against the labels (see docs/sources.md). `fetch.py` downloads the same files again from the
3W repository on GitHub.

## Source and licence

- Dataset: Petróleo Brasileiro S.A. (Petrobras). *The 3W Project.* <https://github.com/petrobras/3W>,
  dataset version 2.0.0.
- Paper: Vaz Vargas, R. E., Munaro, C. J., Marques Ciarelli, P., Gonçalves Medeiros, A., Guberfain do
  Amaral, B., Centurion Barrionuevo, D., Dias de Araújo, J. C., Lins Ribeiro, J., & Pierezan Magalhães,
  L. (2019). *A realistic and public dataset with rare undesirable real events in oil wells.* Journal of
  Petroleum Science and Engineering, 181. <https://doi.org/10.1016/j.petrol.2019.106223>
- Version 2: Vaz Vargas, R. E., de Melo Junior, A. J., de Campos Lima, C. B. et al. (2026). *3W Dataset
  2.0.0: a realistic and public dataset with rare undesirable real events in oil wells.* Scientific
  Data 13, 949. <https://doi.org/10.1038/s41597-026-07225-z>

The data files in `data/` are © Petrobras and licensed under **Creative Commons Attribution 4.0
International** (`LICENSE`). They are redistributed unchanged, apart from the folder layout and file
names: `dataset/<event>/<instance>.parquet` in the 3W repository is `data/c<event>_<instance>.parquet`
here. This folder is left out of the Docker image (`.dockerignore`).
