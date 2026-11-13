# PID2Graph slice

A small slice of **PID2Graph** (Stürmer, Graumann and Koch, DLR, 2025), for scoring how well Fuzer
reads the topology of a P&ID drawing it didn't make.

- `open100/`: all 12 real drawings from the OPEN100 set, as PNG, with their ground-truth graphs (`.graphml`).
- `synthetic/`: the first 20 synthetic drawings (JPEG, 7000 × 4500), with their graphs.

In each graph, symbols are nodes with a class label (valve, pump, tank, instrumentation, general,
inlet/outlet, arrow, background) and a bounding box in image pixels. Lines are edges, labelled solid
or non-solid. `connector` and `crossing` nodes mark where a line meets a symbol or another line. The
graphs carry no tag text. `fuzer.evals.pid2graph` reads them and scores a reading against them.

`fetch.py` downloads the same files again from Zenodo with range requests, without the 9.3 GB zip.
The full dataset also has the 500-sheet synthetic set, the Dataset-P&ID drawings and patched tiles.
None of those are kept here.

## Source and licence

- Dataset: Stürmer, J. M., Graumann, M., Koch, T. *PID2Graph*. Zenodo, 2025.
  <https://doi.org/10.5281/zenodo.14803338>
- Paper: Stürmer, J. M., Graumann, M., & Koch, T. (2025). *From Engineering Diagrams to Graphs:
  Digitizing P&IDs with Transformers.* IEEE DSAA 2025. <https://doi.org/10.1109/DSAA65442.2025.11248012>

The files in this folder are © their authors and licensed under
**Creative Commons Attribution-ShareAlike 4.0 International** (`LICENSE`). They are redistributed
unchanged, apart from the folder layout and file names, which are flattened from
`PID2Graph/Complete/PID2Graph OPEN100/` and `.../PID2Graph Synthetic/`.

The share-alike terms cover only the dataset files in this folder. The rest of the repository,
including the code that reads and scores them, is not a derivative of the dataset and keeps its own
terms. This folder is left out of the Docker image (`.dockerignore`).
