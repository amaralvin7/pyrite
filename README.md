# pyrite

pyrite (Particle cYcling Rates from Inversion of Tracers in the ocEan) is an inverse model that uses chemical tracer data obtained from the [NASA EXPORTS](https://oceanexports.org/) North Pacific campaign to infer rates of particle cycling processes in the ocean. The model currently uses concentrations of particulate organic carbon (POC) to estimate rates of particle production, settling, remineralization, aggregation, disaggregation, and transport from zooplankton diel vertical migration.

I'm also currently working on [mpic](https://github.com/amaralvin7/mpic), which is a project aimed at classifying images of marine particles and estimating the carbon fluxes that these particles contribute as they sink down the water column.

## Citation
Amaral, VJ, Lam, PJ, Marchal, O, Roca-Martí, M, Fox, J, Nelson, NB. 2022. Particle cycling rates at Station P as estimated from the inversion of POC concentration data. *Elementa: Science of the Anthropocene*, 10(1). DOI: https://doi.org/10.1525/elementa.2021.00018

Amaral, VJ, Lam, PJ, Marchal, O, Kenyon, JA. 2024. Cycling rates of particulate organic carbon along the GEOTRACES Pacific meridional transect GP15. *Global Biogeochemical Cycles*, 38. DOI: https://doi.org/10.1029/2023GB007940

## Installation (command line)
Conda must be installed ([Miniconda](https://docs.conda.io/projects/conda/en/latest/glossary.html#miniconda-glossary) recommended). After cloning the repository, create a new virtual environment and download all required dependencies:
```
conda env create --name pyrite --file environment.yml
```
Install the package locally:
```
pip install -e .
```

## Usage
The primary purpose of this release is to allow any user to replicate the results found in Amaral et al. (*Elementa*, 2022) and Amaral et al. (*GBC*, 2024).

### Amaral et al. (*Elementa*, 2022)

1. Run all data inversions:
```
cd /scripts/exports/
python run.py
```

2. Run the twin experiment:
```
python runTE.py
```
Steps 1 and 2 will save model results (stored as pickled dictionaries) in ```./results/exports/*.pkl```.

3. Generate the output summary text file that contains all numerical values found in the manuscript:
```
python summary.py
```
The summary will be saved in ```./results/exports/out.txt```.

4. Generate all figures found in the manuscript:
```
python figures.py
```
Figures will be saved in ```./results/exports/figures/```.

### Amaral et al. (*GBC*, 2024)

1. Run all data inversions:
```
cd /scripts/geotraces/
python run.py
```

This will save results from each inversion as an HDF5 file in ```./results/geotraces/output/*.h5```.

2. Compile individual inversion files into a single file for easier manipulation:
```
python compile_output.py
```
The resulting file will be saved as ```./results/geotraces/output.h5```.

3. Generate all figures found in the manuscript:
```
python figures.py
```
Figures will be saved in ```./results/geotraces/figures/```.

*Note: These instructions were tested on a Linux machine.*


## Acknowledgements
The structure of this repository was inspired by *[The Good Research Code Handbook](https://goodresearch.dev/index.html)* by Patrick Mineault.

## License
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
