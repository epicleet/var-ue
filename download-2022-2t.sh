#!/bin/bash -xe

mkdir -p data/{orig,unpack}

for estado in AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO ZZ; do
    zipfile="bu_imgbu_logjez_rdv_vscmr_2022_2t_$estado.zip"

    pushd "data/orig"
    wget -c "https://cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes2022/arqurnatot/$zipfile"
    popd

    mkdir -p "data/unpack/$estado"
    pushd "data/unpack/$estado"
    unzip -u "../../orig/$zipfile"
    popd
done 
