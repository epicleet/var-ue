# VAR UE - Verificador de Assinaturas de Resultados das Urnas Eletrônicas

Este script verifica as assinaturas geradas pelo Módulo de Segurança Embarcado (MSE) da urna eletrônica brasileira (modelos UE2009 a UE2020).

Além disso, o script realiza uma tarefa considerada *impossível* por alguns (mas, na prática, bastante trivial): recupera o Código Identificador da UE a partir do campo Common Name do certificado digital, relacionando-o assim de forma unívoca aos logs gerados por aquela urna, assim como aos demais produtos públicos da eleição (BU e RDV).


## Instalação

1. Instale o [python-poetry](https://python-poetry.org/docs/#installing-with-the-official-installer)

2. Clone o repositório e gere o virtualenv usando o poetry:
   ```
   git clone https://github.com/epicleet/var-ue.git
   cd var-ue

   poetry install
   ```


## Baixando os arquivos do segundo turno de 2022

Para baixar e descompactar os arquivos da eleição, execute:

```
./download-2022-2t.sh
```


## Exemplos de uso

### Verificando uma urna específica

Execute o VAR UE passando o nome de um arquivo com extensão .vscmr ou .vscsa, por exemplo:

```
poetry run python var-ue.py data/unpack/SP/o00407-6239103690001.vscmr
```

O comando acima gerará uma saída parecida com a seguinte:

```
2022-11-29 16:27:18,779 - INFO - data/unpack/SP/o00407-6239103690001.vscmr - Identificação da urna: ueao01793429
2022-11-29 16:27:18,854 - INFO - o00407-6239103690001.bu - OK
2022-11-29 16:27:18,929 - INFO - o00407-6239103690001.rdv - OK
2022-11-29 16:27:19,003 - INFO - o00407-6239103690001.imgbu - OK
2022-11-29 16:27:19,078 - INFO - o00407-6239103690001.logjez - OK
```

Interpretando o resultado do exemplo acima:

* O Código Identificador da UE é **01793429**, e pôde ser determinado mesmo tratando-se de uma urna modelo UE2015.

* As assinaturas dos arquivos .bu, .rdv, .imgbu e .logjez produzidos por essa urna foram verificadas com sucesso.


### Verificando os dados de todas as urnas

Execute o VAR UE passando o nome de um diretório:

```
poetry run python var-ue.py data/unpack
```

Se você não se importar em receber uma saída bagunçada, você pode acrescentar a opção `--parallel` para paralelizar o processamento, utilizando todos os núcleos da sua máquina:

```
poetry run python var-ue.py --parallel data/unpack
```

