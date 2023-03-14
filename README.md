Cellosaurus API
===============

From the CALIPHO group of the SIB - Swiss Institute of Bioinformatics

Provides a programmatic access to the Cellosaurus, a knowledge resource on cell lines

Cellosaurus API : https://api.cellosaurus.org/

See also cellosaurus website : https://www.cellosaurus.org/

## Prerequisites to local installation

* Linux or Mac platofrm 
* git installed
* curl or equivalent installed
* python 3.9 installed, see cellapi_builder.py and main.py for modules to be installed
* solr 8.11.1 or later installed

## Install latest cellosaurus-api code

```shell
code_source="https://github.com/calipho-sib/cellosaurus-api.git" 
git checkout $code_source
cd cellosaurus-api
```

## Setup of solr collection for cellosaurus-api  (only on first installation)

* Create a symbolic link 'solr' in the api directory pointing to your to your solr install directory

```shell
ln -s /your/solr8.11.1-or-later/home/directory/ solr
```

* Start solr service, create cellosaurus solr collection 'pamcore1'

```shell
./solr_service.sh start
./solr/bin/solr create -c pamcore1
```

* Link cellosaurus-api solr config to 'pamcore1' collection

```shell
cd solr/server/solr/poumcore1/conf
mv managed-schema managed-schema.ori 
mv solrconfig.xml solrconfig.xml.ori
ln -s ../../../../../solr_config/schema.xml
ln -s ../../../../../solr_config/solrconfig.xml
```

* Back to cellosaurus-api base directory and restart service to take into account new schema of 'pamcore1' collection

```shell
cd ../../../../../
./solr_service.sh restart
```

## Retrieve latest cellosaurus data

```shell
data_source="https://ftp.expasy.org/databases/cellosaurus"
mkdir -p data_in
cd data_in
curl "$code_source/cellosaurus.txt" > cellosaurus.txt
curl "$code_source/cellosaurus_refs.txt" > cellosaurus_refs.txt
curl "$code_source/cellosaurus.xml" > cellosaurus.xml
```

## Build api indexes and solr data

* Go back to cellosaurus-api base directory

```shell
cd .. 
```

* Build api index files in serial directory

```shell
mkdir -p serial
python3.9 cellapi_builder.py BUILD
```

* Build solr input files in solr_data directory (long process, few minutes)

```shell
mkdir -p solr_data
python3.9 cellapi_builder.py SOLR
```

* Reload solr data in solr 'pamcore1' collection

```shell
./solr_service.sh restart
./solr/bin/post -c pamcore1 -d "<delete><query>*:*</query></delete>"
./solr/bin/post -c pamcore1 solr_data/data*.xml
```

## How to restart services

```shell
./api_service.sh stop
./api_service.sh start
./solr_service.sh stop
./solr_service.sh start
```

## How to connect to services

Use your browser and go to
* http://localhost:8088/
* http://localhost:8983/solr

## Reference

Bairoch A.
The Cellosaurus, a cell line knowledge resource.
J. Biomol. Tech. 29:25-38(2018).
DOI: 10.7171/jbt.18-2902-002; PMID: 29805321
https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5945021/


## Licensing

[![CC BY 4.0][cc-by-shield]][cc-by]

This work is licensed under a [Creative Commons Attribution 4.0 International
License][cc-by].

[![CC BY 4.0][cc-by-image]][cc-by]

[cc-by]: http://creativecommons.org/licenses/by/4.0/
[cc-by-image]: https://i.creativecommons.org/l/by/4.0/88x31.png
[cc-by-shield]: https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey.svg
