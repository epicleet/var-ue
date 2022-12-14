from ecpy.curves import Curve
from ecpy.keys import ECPublicKey
from ecpy.ecdsa import ECDSA
from ecpy.eddsa import EDDSA
from itertools import chain
from base64 import b64decode
from glob import iglob
import asn1tools
import argparse
import logging
import hashlib
import os


def main():
    parser = argparse.ArgumentParser(
        description="Imprime o ID_UE e valida assinaturas da Urna Eletrônica (UE)",
        formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--debug",
                        action="store_true",
                        help="ativa o modo DEBUG")
    parser.add_argument("--parallel",
                        action="store_true",
                        help="paraleliza verificação em múltiplos núcleos")
    parser.add_argument("path",
                        nargs='+',
                        help="arquivos .vsc* ou diretórios contendo arquivos .vsc*")

    args = parser.parse_args()

    level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s - %(levelname)s - %(message)s")

    paths = []
    for path in args.path:
        if os.path.isdir(path):
            path = os.path.join(path, '**', '*.vsc*')
        paths.append(path)

    vscmr_files = chain(*[iglob(path, recursive=True) for path in paths])
    if args.parallel:
        from multiprocessing import Pool
        p = Pool()
        for _ in p.imap_unordered(verify_file, vscmr_files):
            pass
    else:
        for vscmr_filename in vscmr_files:
            verify_file(vscmr_filename)


def verify_file(vscmr_filename):
    logging.debug(f'{vscmr_filename} - Iniciando o processamento')

    with open(vscmr_filename, 'rb') as f:
        vscmr_encoded = f.read()
    vscmr = vscmr_conv.decode('EntidadeAssinaturaResultado', vscmr_encoded)

    cert_data = vscmr['assinaturaHW']['certificadoDigital']
    if cert_data.startswith(b'-----'):
        # PEM to DER
        cert_data = b64decode(b''.join(cert_data.splitlines()[1:-1]))
    cert = x509_conv.decode('Certificate', cert_data)
    tbscert = cert['tbsCertificate']

    cn = get_cn(tbscert['subject'])
    logging.info(f'{vscmr_filename} - Identificação da urna: {cn}')

    pubkey, signer = get_pubkey_and_signer(tbscert)
    logging.debug(f'{vscmr_filename} - Chave pública com curva {pubkey.curve.name}')

    issuer_cn = get_cn(tbscert['issuer'])
    logging.debug(f'{vscmr_filename} - Certificado emitido por {issuer_cn}')
    issuer_pubkey = trusted_issuers[get_cn(tbscert['issuer'])]
    msg = x509_conv.encode('TBSCertificate', tbscert)
    if isinstance(signer, ECDSA):
        msg = hashlib.sha512(msg).digest()
    if not signer.verify(msg, cert['signature'][0], issuer_pubkey):
        log_and_raise(f'{vscmr_filename} - assinatura do certificado inválida')
    # TODO: receber data das eleições como argumento e verificar a validade do certificado

    conteudo = vscmr_conv.decode('Assinatura', vscmr['assinaturaHW']['conteudoAutoAssinado'])
    assinaturas = conteudo['arquivosAssinados']

    base_path = os.path.dirname(vscmr_filename)

    for assinatura in assinaturas:
        arquivo = assinatura['nomeArquivo']
        expected_hash = assinatura['assinatura']['hash']
        sig = assinatura['assinatura']['assinatura']

        path = os.path.join(base_path, arquivo)

        if not os.path.exists(path):
            logging.debug(f'{arquivo} - não encontrado')
            continue

        with open(path, 'rb') as f:
            h = hashlib.sha512(f.read()).digest()

        if h != expected_hash:
            log_and_raise(f'{arquivo} - hash inválido')

        if not signer.verify(hashlib.sha512(h).digest(), sig, pubkey):
            log_and_raise(f'{arquivo} - assinatura inválida')

        logging.info(f'{arquivo} - OK')


def decode_issuers(issuers):
    res = {}
    for encoded_cert in issuers:
        cert = x509_conv.decode('Certificate', b64decode(encoded_cert)) 
        pubkey, _ = get_pubkey_and_signer(cert['tbsCertificate'])
        res[get_cn(cert['tbsCertificate']['subject'])] = pubkey
    return res


def get_cn(name):
    cn = next(
            item['value'] for item in name[1][0]
            if item['type'] == '2.5.4.3')
    assert cn[0] in {0x13, 0xc}, 'CN deveria ser PrintableString ou OCTET STRING'
    cn = cn[2:2+cn[1]].decode('utf-8')
    return cn


def get_pubkey_and_signer(tbscert):
    pubkey_algo = tbscert['subjectPublicKeyInfo']['algorithm']['algorithm']
    encoded_pubkey, _ = tbscert['subjectPublicKeyInfo']['subjectPublicKey']

    if pubkey_algo == '1.2.840.10045.2.1':
        signer = ECDSA()
        curve = Curve.get_curve('secp521r1')
    elif pubkey_algo == '1.3.6.1.4.1.44588.2.1':
        signer = EDDSA(hashlib.shake_256, hash_len=132)
        curve = Curve.get_curve('Ed521')
    else:
        raise ValueError(f'Este utilitário não suporta chave pública do tipo {pubkey_algo}')

    pubkey = ECPublicKey(curve.decode_point(encoded_pubkey))

    return pubkey, signer


def log_and_raise(msg):
    e = Exception(msg)
    logging.exception(e)
    raise e


vscmr_spec = '''
ModuloAssinaturaResultado DEFINITIONS IMPLICIT TAGS ::= BEGIN

EXPORTS ALL;

-- TIPOS
DataHoraJE ::= GeneralString(SIZE(15))  -- Data e hora utilizada pela Justiça Eleitoral no formato YYYYMMDDThhmmss.

-- ENUMS
--Tipos de algoritmos de assinatura (cepesc é o algoritmo padrão (ainda não há previsão de uso dos demais)).
AlgoritmoAssinatura ::= ENUMERATED {
    rsa(1),
    ecdsa(2),
    cepesc(3)
}

-- Tipos de algoritmos de hash (Todos os algoritmos devem ser suportados, mas sha512 é o padrão).
AlgoritmoHash ::= ENUMERATED {
    sha1(1),
    sha256(2),
    sha384(3),
    sha512(4)
}

-- Tipos de modelos de urna eletrônica.
ModeloUrna ::= ENUMERATED {
    ue2009(9),  -- Urna modelo 2009.
    ue2010(10), -- Urna modelo 2010.
    ue2011(11), -- Urna modelo 2011.
    ue2013(13), -- Urna modelo 2013.
    ue2015(15), -- Urna modelo 2015.
    ue2020(20)  -- Urna modelo 2020.
}

-- ENVELOPE
-- Entidade que engloba a lista de assinaturas utilizadas para assinar os arquivos para manter a integridade e segurança dos dados.
EntidadeAssinatura ::= SEQUENCE {
    dataHoraCriacao         DataHoraJE,                         -- Data e Hora da criacao do arquivo.
    versao                  INTEGER (2..99999999),              -- Versao do protocolo (Alterações devem gerar novo valor. Nas eleições de 2012 foi utilizado o enumerado de valor 1, a partir de 2014 utilizar o valor 2).
    autoAssinado            AutoAssinaturaDigital,              -- Informações da auto assinatura digital.
    conteudoAutoAssinado    OCTET STRING,                       -- Conteúdo da assinatura do próprio arquivo.
    certificadoDigital      OCTET STRING OPTIONAL,              -- Certificado digital da urna eletrônica.
    conjuntoChave           GeneralString(SIZE(1..15)) OPTIONAL -- Identificador do conjunto de chaves usado para assinar o pacote.
}

-- Entidade responsável por gerar o arquivo de assinatura de todos os arquivos de resultados da urna.
-- Podendo ter dois tipos de assinatura (Hardware (HW) e Software (SW)).
-- Esses arquivos são informados na Mídia de Resultado quando a urna eletrônica é encerrada.
EntidadeAssinaturaResultado ::= SEQUENCE {
    modeloUrna      ModeloUrna,         -- Modelo da urna eletrônica.
    assinaturaSW    EntidadeAssinatura, -- Assinatura realizada via software (normalmente CEPESC).
    assinaturaHW    EntidadeAssinatura  -- Assinatura realizada via hardware de segurança da urna eletrônica.
}

-- Demais SEQUENCES
-- Informações do algoritmo de hash.
-- Informações do algoritmo de assinatura .
AlgoritmoAssinaturaInfo ::= SEQUENCE {
    algoritmo   AlgoritmoAssinatura,    -- Tipo do algoritmo de assinatura.
    bits        INTEGER                 -- Tamanho da assinatura.
}

AlgoritmoHashInfo ::= SEQUENCE {
    algoritmo AlgoritmoHash -- Tipo do algoritmo de hash.
}

-- Informações dos arquivos assinados.
Assinatura ::= SEQUENCE {
    arquivosAssinados SEQUENCE OF AssinaturaArquivo -- Lista com Informações dos arquivos assinados.
}

--Informações do arquivo e da assinatura.
AssinaturaArquivo ::= SEQUENCE {
    nomeArquivo GeneralString,      -- Nome do arquivo.
    assinatura  AssinaturaDigital   -- Assinatura digital do arquivo.
}

-- Informações da assinatura digital
AssinaturaDigital ::= SEQUENCE {
    tamanho     INTEGER,        -- Tamanho da assinatura.
    hash        OCTET STRING,   -- Hash da assinatura (Deve ser calculado uma única vez e ser utilizado também para o cálculo da assinatura).
    assinatura  OCTET STRING    -- Assinatura (Gerado/verificado a partir do hash acima).
}

-- Informações da auto assinatura digital.
AutoAssinaturaDigital ::= SEQUENCE {
    usuario             DescritorChave,             -- Nome do usuário (Geralmente uma seção) que realizou a assinatura do arquivo.
    algoritmoHash       AlgoritmoHashInfo,          -- Algoritmo de hash utilizado para realizar a assinatura (Será o mesmo para as assinaturas de arquivos).
    algoritmoAssinatura AlgoritmoAssinaturaInfo,    -- Algoritmo utilizado para realizar a assinatura (Será o mesmo para as assinaturas de arquivos).
    assinatura          AssinaturaDigital           -- Informações da assinatura digital.
}

-- Identificador com informações da assinatura.
DescritorChave ::= SEQUENCE {
    nomeUsuario GeneralString,  -- Nome do usuário (Geralmente uma seção) que realizou a assinatura no arquivo.
    serial      INTEGER         -- Data em que foi gerado o conjunto de chaves.
}

END
'''

x509_spec='''
PKIX1Explicit88 { iso(1) identified-organization(3) dod(6) internet(1)
  security(5) mechanisms(5) pkix(7) id-mod(0) id-pkix1-explicit(18) }

DEFINITIONS EXPLICIT TAGS ::=

BEGIN

-- EXPORTS ALL --

-- IMPORTS NONE --

-- UNIVERSAL Types defined in 1993 and 1998 ASN.1
-- and required by this specification

-- UniversalString ::= [UNIVERSAL 28] IMPLICIT OCTET STRING
        -- UniversalString is defined in ASN.1:1993

-- BMPString ::= [UNIVERSAL 30] IMPLICIT OCTET STRING
      -- BMPString is the subtype of UniversalString and models
      -- the Basic Multilingual Plane of ISO/IEC 10646

-- UTF8String ::= [UNIVERSAL 12] IMPLICIT OCTET STRING
      -- The content of this type conforms to RFC 3629.

-- PKIX specific OIDs

id-pkix  OBJECT IDENTIFIER  ::=
         { iso(1) identified-organization(3) dod(6) internet(1)
                    security(5) mechanisms(5) pkix(7) }

-- PKIX arcs

id-pe OBJECT IDENTIFIER ::= { id-pkix 1 }
        -- arc for private certificate extensions
id-qt OBJECT IDENTIFIER ::= { id-pkix 2 }
        -- arc for policy qualifier types
id-kp OBJECT IDENTIFIER ::= { id-pkix 3 }
        -- arc for extended key purpose OIDS
id-ad OBJECT IDENTIFIER ::= { id-pkix 48 }
        -- arc for access descriptors

-- policyQualifierIds for Internet policy qualifiers

id-qt-cps      OBJECT IDENTIFIER ::=  { id-qt 1 }
      -- OID for CPS qualifier
id-qt-unotice  OBJECT IDENTIFIER ::=  { id-qt 2 }
      -- OID for user notice qualifier

-- access descriptor definitions

id-ad-ocsp         OBJECT IDENTIFIER ::= { id-ad 1 }
id-ad-caIssuers    OBJECT IDENTIFIER ::= { id-ad 2 }
id-ad-timeStamping OBJECT IDENTIFIER ::= { id-ad 3 }
id-ad-caRepository OBJECT IDENTIFIER ::= { id-ad 5 }

-- attribute data types

Attribute               ::= SEQUENCE {
      type             AttributeType,
      values    SET OF AttributeValue }
            -- at least one value is required

AttributeType           ::= OBJECT IDENTIFIER

AttributeValue          ::= ANY -- DEFINED BY AttributeType

AttributeTypeAndValue   ::= SEQUENCE {
        type    AttributeType,
        value   AttributeValue }

-- suggested naming attributes: Definition of the following
--   information object set may be augmented to meet local
--   requirements.  Note that deleting members of the set may
--   prevent interoperability with conforming implementations.
-- presented in pairs: the AttributeType followed by the
--   type definition for the corresponding AttributeValue

-- Arc for standard naming attributes

id-at OBJECT IDENTIFIER ::= { joint-iso-ccitt(2) ds(5) 4 }

-- Naming attributes of type X520name

id-at-name                AttributeType ::= { id-at 41 }
id-at-surname             AttributeType ::= { id-at  4 }
id-at-givenName           AttributeType ::= { id-at 42 }
id-at-initials            AttributeType ::= { id-at 43 }
id-at-generationQualifier AttributeType ::= { id-at 44 }

-- Naming attributes of type X520Name:
--   X520name ::= DirectoryString (SIZE (1..ub-name))
--
-- Expanded to avoid parameterized type:
X520name ::= CHOICE {
      teletexString     TeletexString   (SIZE (1..ub-name)),
      printableString   PrintableString (SIZE (1..ub-name)),
      universalString   UniversalString (SIZE (1..ub-name)),
      utf8String        UTF8String      (SIZE (1..ub-name)),
      bmpString         BMPString       (SIZE (1..ub-name)) }

-- Naming attributes of type X520CommonName

id-at-commonName        AttributeType ::= { id-at 3 }

-- Naming attributes of type X520CommonName:
--   X520CommonName ::= DirectoryName (SIZE (1..ub-common-name))
--
-- Expanded to avoid parameterized type:
X520CommonName ::= CHOICE {
      teletexString     TeletexString   (SIZE (1..ub-common-name)),
      printableString   PrintableString (SIZE (1..ub-common-name)),
      universalString   UniversalString (SIZE (1..ub-common-name)),
      utf8String        UTF8String      (SIZE (1..ub-common-name)),
      bmpString         BMPString       (SIZE (1..ub-common-name)) }

-- Naming attributes of type X520LocalityName

id-at-localityName      AttributeType ::= { id-at 7 }

-- Naming attributes of type X520LocalityName:
--   X520LocalityName ::= DirectoryName (SIZE (1..ub-locality-name))
--
-- Expanded to avoid parameterized type:
X520LocalityName ::= CHOICE {
      teletexString     TeletexString   (SIZE (1..ub-locality-name)),
      printableString   PrintableString (SIZE (1..ub-locality-name)),
      universalString   UniversalString (SIZE (1..ub-locality-name)),
      utf8String        UTF8String      (SIZE (1..ub-locality-name)),
      bmpString         BMPString       (SIZE (1..ub-locality-name)) }

-- Naming attributes of type X520StateOrProvinceName

id-at-stateOrProvinceName AttributeType ::= { id-at 8 }

-- Naming attributes of type X520StateOrProvinceName:
--   X520StateOrProvinceName ::= DirectoryName (SIZE (1..ub-state-name))
--
-- Expanded to avoid parameterized type:
X520StateOrProvinceName ::= CHOICE {
      teletexString     TeletexString   (SIZE (1..ub-state-name)),
      printableString   PrintableString (SIZE (1..ub-state-name)),
      universalString   UniversalString (SIZE (1..ub-state-name)),
      utf8String        UTF8String      (SIZE (1..ub-state-name)),
      bmpString         BMPString       (SIZE (1..ub-state-name)) }

-- Naming attributes of type X520OrganizationName

id-at-organizationName  AttributeType ::= { id-at 10 }

-- Naming attributes of type X520OrganizationName:
--   X520OrganizationName ::=
--          DirectoryName (SIZE (1..ub-organization-name))
--
-- Expanded to avoid parameterized type:
X520OrganizationName ::= CHOICE {
      teletexString     TeletexString
                          (SIZE (1..ub-organization-name)),
      printableString   PrintableString
                          (SIZE (1..ub-organization-name)),
      universalString   UniversalString
                          (SIZE (1..ub-organization-name)),
      utf8String        UTF8String
                          (SIZE (1..ub-organization-name)),
      bmpString         BMPString
                          (SIZE (1..ub-organization-name))  }

-- Naming attributes of type X520OrganizationalUnitName

id-at-organizationalUnitName AttributeType ::= { id-at 11 }

-- Naming attributes of type X520OrganizationalUnitName:
--   X520OrganizationalUnitName ::=
--          DirectoryName (SIZE (1..ub-organizational-unit-name))
--
-- Expanded to avoid parameterized type:
X520OrganizationalUnitName ::= CHOICE {
      teletexString     TeletexString
                          (SIZE (1..ub-organizational-unit-name)),
      printableString   PrintableString
                          (SIZE (1..ub-organizational-unit-name)),
      universalString   UniversalString
                          (SIZE (1..ub-organizational-unit-name)),
      utf8String        UTF8String
                          (SIZE (1..ub-organizational-unit-name)),
      bmpString         BMPString
                          (SIZE (1..ub-organizational-unit-name)) }

-- Naming attributes of type X520Title

id-at-title             AttributeType ::= { id-at 12 }

-- Naming attributes of type X520Title:
--   X520Title ::= DirectoryName (SIZE (1..ub-title))
--
-- Expanded to avoid parameterized type:
X520Title ::= CHOICE {
      teletexString     TeletexString   (SIZE (1..ub-title)),
      printableString   PrintableString (SIZE (1..ub-title)),
      universalString   UniversalString (SIZE (1..ub-title)),
      utf8String        UTF8String      (SIZE (1..ub-title)),
      bmpString         BMPString       (SIZE (1..ub-title)) }

-- Naming attributes of type X520dnQualifier

id-at-dnQualifier       AttributeType ::= { id-at 46 }

X520dnQualifier ::=     PrintableString

-- Naming attributes of type X520countryName (digraph from IS 3166)

id-at-countryName       AttributeType ::= { id-at 6 }

X520countryName ::=     PrintableString (SIZE (2))

-- Naming attributes of type X520SerialNumber

id-at-serialNumber      AttributeType ::= { id-at 5 }

X520SerialNumber ::=    PrintableString (SIZE (1..ub-serial-number))

-- Naming attributes of type X520Pseudonym

id-at-pseudonym         AttributeType ::= { id-at 65 }

-- Naming attributes of type X520Pseudonym:
--   X520Pseudonym ::= DirectoryName (SIZE (1..ub-pseudonym))
--
-- Expanded to avoid parameterized type:
X520Pseudonym ::= CHOICE {
   teletexString     TeletexString   (SIZE (1..ub-pseudonym)),
   printableString   PrintableString (SIZE (1..ub-pseudonym)),
   universalString   UniversalString (SIZE (1..ub-pseudonym)),
   utf8String        UTF8String      (SIZE (1..ub-pseudonym)),
   bmpString         BMPString       (SIZE (1..ub-pseudonym)) }

-- Naming attributes of type DomainComponent (from RFC 4519)

id-domainComponent   AttributeType ::= { 0 9 2342 19200300 100 1 25 }

DomainComponent ::=  IA5String

-- Legacy attributes

pkcs-9 OBJECT IDENTIFIER ::=
       { iso(1) member-body(2) us(840) rsadsi(113549) pkcs(1) 9 }

id-emailAddress      AttributeType ::= { pkcs-9 1 }

EmailAddress ::=     IA5String (SIZE (1..ub-emailaddress-length))

-- naming data types --

Name ::= CHOICE { -- only one possibility for now --
      rdnSequence  RDNSequence }

RDNSequence ::= SEQUENCE OF RelativeDistinguishedName

DistinguishedName ::=   RDNSequence

RelativeDistinguishedName ::= SET SIZE (1..MAX) OF AttributeTypeAndValue

-- Directory string type --

DirectoryString ::= CHOICE {
      teletexString       TeletexString   (SIZE (1..MAX)),
      printableString     PrintableString (SIZE (1..MAX)),
      universalString     UniversalString (SIZE (1..MAX)),
      utf8String          UTF8String      (SIZE (1..MAX)),
      bmpString           BMPString       (SIZE (1..MAX)) }

-- certificate and CRL specific structures begin here

Certificate  ::=  SEQUENCE  {
     tbsCertificate       TBSCertificate,
     signatureAlgorithm   AlgorithmIdentifier,
     signature            BIT STRING  }

TBSCertificate  ::=  SEQUENCE  {
     version        [0] Version DEFAULT v1,
     serialNumber         CertificateSerialNumber,
     signature            AlgorithmIdentifier,
     issuer               Name,
     validity             Validity,
     subject              Name,
     subjectPublicKeyInfo SubjectPublicKeyInfo,
     issuerUniqueID  [1]  IMPLICIT UniqueIdentifier OPTIONAL,
                          -- If present, version MUST be v2 or v3
     subjectUniqueID [2]  IMPLICIT UniqueIdentifier OPTIONAL,
                          -- If present, version MUST be v2 or v3
     extensions      [3]  Extensions OPTIONAL
                          -- If present, version MUST be v3 -- }

Version  ::=  INTEGER  {  v1(0), v2(1), v3(2)  }

CertificateSerialNumber  ::=  INTEGER

Validity ::= SEQUENCE {
     notBefore      Time,
     notAfter       Time  }

Time ::= CHOICE {
     utcTime        UTCTime,
     generalTime    GeneralizedTime }

UniqueIdentifier  ::=  BIT STRING

SubjectPublicKeyInfo  ::=  SEQUENCE  {
     algorithm            AlgorithmIdentifier,
     subjectPublicKey     BIT STRING  }

Extensions  ::=  SEQUENCE SIZE (1..MAX) OF Extension

Extension  ::=  SEQUENCE  {
     extnID      OBJECT IDENTIFIER,
     critical    BOOLEAN DEFAULT FALSE,
     extnValue   OCTET STRING
                 -- contains the DER encoding of an ASN.1 value
                 -- corresponding to the extension type identified
                 -- by extnID
     }

-- CRL structures

CertificateList  ::=  SEQUENCE  {
     tbsCertList          TBSCertList,
     signatureAlgorithm   AlgorithmIdentifier,
     signature            BIT STRING  }

TBSCertList  ::=  SEQUENCE  {
     version                 Version OPTIONAL,
                                   -- if present, MUST be v2
     signature               AlgorithmIdentifier,
     issuer                  Name,
     thisUpdate              Time,
     nextUpdate              Time OPTIONAL,
     revokedCertificates     SEQUENCE OF SEQUENCE  {
          userCertificate         CertificateSerialNumber,
          revocationDate          Time,
          crlEntryExtensions      Extensions OPTIONAL
                                   -- if present, version MUST be v2
                               }  OPTIONAL,
     crlExtensions           [0] Extensions OPTIONAL }
                                   -- if present, version MUST be v2

-- Version, Time, CertificateSerialNumber, and Extensions were
-- defined earlier for use in the certificate structure

AlgorithmIdentifier  ::=  SEQUENCE  {
     algorithm               OBJECT IDENTIFIER,
     parameters              ANY DEFINED BY algorithm OPTIONAL  }
                                -- contains a value of the type
                                -- registered for use with the
                                -- algorithm object identifier value

-- X.400 address syntax starts here

ORAddress ::= SEQUENCE {
   built-in-standard-attributes BuiltInStandardAttributes,
   built-in-domain-defined-attributes
                   BuiltInDomainDefinedAttributes OPTIONAL,
   -- see also teletex-domain-defined-attributes
   extension-attributes ExtensionAttributes OPTIONAL }

-- Built-in Standard Attributes

BuiltInStandardAttributes ::= SEQUENCE {
   country-name                  CountryName OPTIONAL,
   administration-domain-name    AdministrationDomainName OPTIONAL,
   network-address           [0] IMPLICIT NetworkAddress OPTIONAL,
     -- see also extended-network-address
   terminal-identifier       [1] IMPLICIT TerminalIdentifier OPTIONAL,
   private-domain-name       [2] PrivateDomainName OPTIONAL,
   organization-name         [3] IMPLICIT OrganizationName OPTIONAL,
     -- see also teletex-organization-name
   numeric-user-identifier   [4] IMPLICIT NumericUserIdentifier
                                 OPTIONAL,
   personal-name             [5] IMPLICIT PersonalName OPTIONAL,
     -- see also teletex-personal-name
   organizational-unit-names [6] IMPLICIT OrganizationalUnitNames
                                 OPTIONAL }
     -- see also teletex-organizational-unit-names

CountryName ::= [APPLICATION 1] CHOICE {
   x121-dcc-code         NumericString
                           (SIZE (ub-country-name-numeric-length)),
   iso-3166-alpha2-code  PrintableString
                           (SIZE (ub-country-name-alpha-length)) }

AdministrationDomainName ::= [APPLICATION 2] CHOICE {
   numeric   NumericString   (SIZE (0..ub-domain-name-length)),
   printable PrintableString (SIZE (0..ub-domain-name-length)) }

NetworkAddress ::= X121Address  -- see also extended-network-address

X121Address ::= NumericString (SIZE (1..ub-x121-address-length))

TerminalIdentifier ::= PrintableString (SIZE (1..ub-terminal-id-length))

PrivateDomainName ::= CHOICE {
   numeric   NumericString   (SIZE (1..ub-domain-name-length)),
   printable PrintableString (SIZE (1..ub-domain-name-length)) }

OrganizationName ::= PrintableString
                            (SIZE (1..ub-organization-name-length))
  -- see also teletex-organization-name

NumericUserIdentifier ::= NumericString
                            (SIZE (1..ub-numeric-user-id-length))

PersonalName ::= SET {
   surname     [0] IMPLICIT PrintableString
                    (SIZE (1..ub-surname-length)),
   given-name  [1] IMPLICIT PrintableString
                    (SIZE (1..ub-given-name-length)) OPTIONAL,
   initials    [2] IMPLICIT PrintableString
                    (SIZE (1..ub-initials-length)) OPTIONAL,
   generation-qualifier [3] IMPLICIT PrintableString
                    (SIZE (1..ub-generation-qualifier-length))
                    OPTIONAL }
  -- see also teletex-personal-name

OrganizationalUnitNames ::= SEQUENCE SIZE (1..ub-organizational-units)
                             OF OrganizationalUnitName
  -- see also teletex-organizational-unit-names

OrganizationalUnitName ::= PrintableString (SIZE
                    (1..ub-organizational-unit-name-length))

-- Built-in Domain-defined Attributes

BuiltInDomainDefinedAttributes ::= SEQUENCE SIZE
                    (1..ub-domain-defined-attributes) OF
                    BuiltInDomainDefinedAttribute

BuiltInDomainDefinedAttribute ::= SEQUENCE {
   type PrintableString (SIZE
                   (1..ub-domain-defined-attribute-type-length)),
   value PrintableString (SIZE
                   (1..ub-domain-defined-attribute-value-length)) }

-- Extension Attributes

ExtensionAttributes ::= SET SIZE (1..ub-extension-attributes) OF
               ExtensionAttribute

ExtensionAttribute ::=  SEQUENCE {
   extension-attribute-type [0] IMPLICIT INTEGER
                   (0..ub-extension-attributes),
   extension-attribute-value [1]
                   ANY DEFINED BY extension-attribute-type }

-- Extension types and attribute values

common-name INTEGER ::= 1

CommonName ::= PrintableString (SIZE (1..ub-common-name-length))

teletex-common-name INTEGER ::= 2

TeletexCommonName ::= TeletexString (SIZE (1..ub-common-name-length))

teletex-organization-name INTEGER ::= 3

TeletexOrganizationName ::=
                TeletexString (SIZE (1..ub-organization-name-length))

teletex-personal-name INTEGER ::= 4

TeletexPersonalName ::= SET {
   surname     [0] IMPLICIT TeletexString
                    (SIZE (1..ub-surname-length)),
   given-name  [1] IMPLICIT TeletexString
                    (SIZE (1..ub-given-name-length)) OPTIONAL,
   initials    [2] IMPLICIT TeletexString
                    (SIZE (1..ub-initials-length)) OPTIONAL,
   generation-qualifier [3] IMPLICIT TeletexString
                    (SIZE (1..ub-generation-qualifier-length))
                    OPTIONAL }

teletex-organizational-unit-names INTEGER ::= 5

TeletexOrganizationalUnitNames ::= SEQUENCE SIZE
      (1..ub-organizational-units) OF TeletexOrganizationalUnitName

TeletexOrganizationalUnitName ::= TeletexString
                  (SIZE (1..ub-organizational-unit-name-length))

pds-name INTEGER ::= 7

PDSName ::= PrintableString (SIZE (1..ub-pds-name-length))

physical-delivery-country-name INTEGER ::= 8

PhysicalDeliveryCountryName ::= CHOICE {
   x121-dcc-code NumericString (SIZE (ub-country-name-numeric-length)),
   iso-3166-alpha2-code PrintableString
                               (SIZE (ub-country-name-alpha-length)) }

postal-code INTEGER ::= 9

PostalCode ::= CHOICE {
   numeric-code   NumericString (SIZE (1..ub-postal-code-length)),
   printable-code PrintableString (SIZE (1..ub-postal-code-length)) }

physical-delivery-office-name INTEGER ::= 10

PhysicalDeliveryOfficeName ::= PDSParameter

physical-delivery-office-number INTEGER ::= 11

PhysicalDeliveryOfficeNumber ::= PDSParameter

extension-OR-address-components INTEGER ::= 12

ExtensionORAddressComponents ::= PDSParameter

physical-delivery-personal-name INTEGER ::= 13

PhysicalDeliveryPersonalName ::= PDSParameter

physical-delivery-organization-name INTEGER ::= 14

PhysicalDeliveryOrganizationName ::= PDSParameter

extension-physical-delivery-address-components INTEGER ::= 15

ExtensionPhysicalDeliveryAddressComponents ::= PDSParameter

unformatted-postal-address INTEGER ::= 16

UnformattedPostalAddress ::= SET {
   printable-address SEQUENCE SIZE (1..ub-pds-physical-address-lines)
        OF PrintableString (SIZE (1..ub-pds-parameter-length)) OPTIONAL,
   teletex-string TeletexString
        (SIZE (1..ub-unformatted-address-length)) OPTIONAL }

street-address INTEGER ::= 17

StreetAddress ::= PDSParameter

post-office-box-address INTEGER ::= 18

PostOfficeBoxAddress ::= PDSParameter

poste-restante-address INTEGER ::= 19

PosteRestanteAddress ::= PDSParameter

unique-postal-name INTEGER ::= 20

UniquePostalName ::= PDSParameter

local-postal-attributes INTEGER ::= 21

LocalPostalAttributes ::= PDSParameter

PDSParameter ::= SET {
   printable-string PrintableString
                (SIZE(1..ub-pds-parameter-length)) OPTIONAL,
   teletex-string TeletexString
                (SIZE(1..ub-pds-parameter-length)) OPTIONAL }

extended-network-address INTEGER ::= 22

ExtendedNetworkAddress ::= CHOICE {
   e163-4-address SEQUENCE {
      number      [0] IMPLICIT NumericString
                       (SIZE (1..ub-e163-4-number-length)),
      sub-address [1] IMPLICIT NumericString
                       (SIZE (1..ub-e163-4-sub-address-length))
                       OPTIONAL },
   psap-address   [0] IMPLICIT PresentationAddress }

PresentationAddress ::= SEQUENCE {
    pSelector     [0] EXPLICIT OCTET STRING OPTIONAL,
    sSelector     [1] EXPLICIT OCTET STRING OPTIONAL,
    tSelector     [2] EXPLICIT OCTET STRING OPTIONAL,
    nAddresses    [3] EXPLICIT SET SIZE (1..MAX) OF OCTET STRING }

terminal-type  INTEGER ::= 23

TerminalType ::= INTEGER {
   telex        (3),
   teletex      (4),
   g3-facsimile (5),
   g4-facsimile (6),
   ia5-terminal (7),
   videotex     (8) } (0..ub-integer-options)

-- Extension Domain-defined Attributes

teletex-domain-defined-attributes INTEGER ::= 6

TeletexDomainDefinedAttributes ::= SEQUENCE SIZE
   (1..ub-domain-defined-attributes) OF TeletexDomainDefinedAttribute

TeletexDomainDefinedAttribute ::= SEQUENCE {
        type TeletexString
               (SIZE (1..ub-domain-defined-attribute-type-length)),
        value TeletexString
               (SIZE (1..ub-domain-defined-attribute-value-length)) }

--  specifications of Upper Bounds MUST be regarded as mandatory
--  from Annex B of ITU-T X.411 Reference Definition of MTS Parameter
--  Upper Bounds

-- Upper Bounds
ub-name INTEGER ::= 32768
ub-common-name INTEGER ::= 64
ub-locality-name INTEGER ::= 128
ub-state-name INTEGER ::= 128
ub-organization-name INTEGER ::= 64
ub-organizational-unit-name INTEGER ::= 64
ub-title INTEGER ::= 64
ub-serial-number INTEGER ::= 64
ub-match INTEGER ::= 128
ub-emailaddress-length INTEGER ::= 255
ub-common-name-length INTEGER ::= 64
ub-country-name-alpha-length INTEGER ::= 2
ub-country-name-numeric-length INTEGER ::= 3
ub-domain-defined-attributes INTEGER ::= 4
ub-domain-defined-attribute-type-length INTEGER ::= 8
ub-domain-defined-attribute-value-length INTEGER ::= 128
ub-domain-name-length INTEGER ::= 16
ub-extension-attributes INTEGER ::= 256
ub-e163-4-number-length INTEGER ::= 15
ub-e163-4-sub-address-length INTEGER ::= 40
ub-generation-qualifier-length INTEGER ::= 3
ub-given-name-length INTEGER ::= 16
ub-initials-length INTEGER ::= 5
ub-integer-options INTEGER ::= 256
ub-numeric-user-id-length INTEGER ::= 32
ub-organization-name-length INTEGER ::= 64
ub-organizational-unit-name-length INTEGER ::= 32
ub-organizational-units INTEGER ::= 4
ub-pds-name-length INTEGER ::= 16
ub-pds-parameter-length INTEGER ::= 30
ub-pds-physical-address-lines INTEGER ::= 6
ub-postal-code-length INTEGER ::= 16
ub-pseudonym INTEGER ::= 128
ub-surname-length INTEGER ::= 40
ub-terminal-id-length INTEGER ::= 24
ub-unformatted-address-length INTEGER ::= 180
ub-x121-address-length INTEGER ::= 16

-- Note - upper bounds on string types, such as TeletexString, are
-- measured in characters.  Excepting PrintableString or IA5String, a
-- significantly greater number of octets will be required to hold
-- such a value.  As a minimum, 16 octets, or twice the specified
-- upper bound, whichever is the larger, should be allowed for

-- TeletexString.  For UTF8String or UniversalString at least four
-- times the upper bound should be allowed.

END

PKIX1Implicit88 { iso(1) identified-organization(3) dod(6) internet(1)
  security(5) mechanisms(5) pkix(7) id-mod(0) id-pkix1-implicit(19) }

DEFINITIONS IMPLICIT TAGS ::=

BEGIN

-- EXPORTS ALL --

IMPORTS
      id-pe, id-kp, id-qt-unotice, id-qt-cps,
      -- delete following line if "new" types are supported --
      BMPString, UTF8String,  -- end "new" types --
      ORAddress, Name, RelativeDistinguishedName,
      CertificateSerialNumber, Attribute, DirectoryString
      FROM PKIX1Explicit88 { iso(1) identified-organization(3)
            dod(6) internet(1) security(5) mechanisms(5) pkix(7)
            id-mod(0) id-pkix1-explicit(18) };

-- ISO arc for standard certificate and CRL extensions

id-ce OBJECT IDENTIFIER  ::=  {joint-iso-ccitt(2) ds(5) 29}

-- authority key identifier OID and syntax

id-ce-authorityKeyIdentifier OBJECT IDENTIFIER ::=  { id-ce 35 }

AuthorityKeyIdentifier ::= SEQUENCE {
    keyIdentifier             [0] KeyIdentifier            OPTIONAL,
    authorityCertIssuer       [1] GeneralNames             OPTIONAL,
    authorityCertSerialNumber [2] CertificateSerialNumber  OPTIONAL }
    -- authorityCertIssuer and authorityCertSerialNumber MUST both
    -- be present or both be absent

KeyIdentifier ::= OCTET STRING

-- subject key identifier OID and syntax

id-ce-subjectKeyIdentifier OBJECT IDENTIFIER ::=  { id-ce 14 }

SubjectKeyIdentifier ::= KeyIdentifier

-- key usage extension OID and syntax

id-ce-keyUsage OBJECT IDENTIFIER ::=  { id-ce 15 }

KeyUsage ::= BIT STRING {
     digitalSignature        (0),
     nonRepudiation          (1),  -- recent editions of X.509 have
                                -- renamed this bit to contentCommitment
     keyEncipherment         (2),
     dataEncipherment        (3),
     keyAgreement            (4),
     keyCertSign             (5),
     cRLSign                 (6),
     encipherOnly            (7),
     decipherOnly            (8) }

-- private key usage period extension OID and syntax

id-ce-privateKeyUsagePeriod OBJECT IDENTIFIER ::=  { id-ce 16 }

PrivateKeyUsagePeriod ::= SEQUENCE {
     notBefore       [0]     GeneralizedTime OPTIONAL,
     notAfter        [1]     GeneralizedTime OPTIONAL }
     -- either notBefore or notAfter MUST be present

-- certificate policies extension OID and syntax

id-ce-certificatePolicies OBJECT IDENTIFIER ::=  { id-ce 32 }

anyPolicy OBJECT IDENTIFIER ::= { id-ce-certificatePolicies 0 }

CertificatePolicies ::= SEQUENCE SIZE (1..MAX) OF PolicyInformation

PolicyInformation ::= SEQUENCE {
     policyIdentifier   CertPolicyId,
     policyQualifiers   SEQUENCE SIZE (1..MAX) OF
             PolicyQualifierInfo OPTIONAL }

CertPolicyId ::= OBJECT IDENTIFIER

PolicyQualifierInfo ::= SEQUENCE {
     policyQualifierId  PolicyQualifierId,
     qualifier          ANY DEFINED BY policyQualifierId }

-- Implementations that recognize additional policy qualifiers MUST
-- augment the following definition for PolicyQualifierId

PolicyQualifierId ::= OBJECT IDENTIFIER ( id-qt-cps | id-qt-unotice )

-- CPS pointer qualifier

CPSuri ::= IA5String

-- user notice qualifier

UserNotice ::= SEQUENCE {
     noticeRef        NoticeReference OPTIONAL,
     explicitText     DisplayText OPTIONAL }

NoticeReference ::= SEQUENCE {
     organization     DisplayText,
     noticeNumbers    SEQUENCE OF INTEGER }

DisplayText ::= CHOICE {
     ia5String        IA5String      (SIZE (1..200)),
     visibleString    VisibleString  (SIZE (1..200)),
     bmpString        BMPString      (SIZE (1..200)),
     utf8String       UTF8String     (SIZE (1..200)) }

-- policy mapping extension OID and syntax

id-ce-policyMappings OBJECT IDENTIFIER ::=  { id-ce 33 }

PolicyMappings ::= SEQUENCE SIZE (1..MAX) OF SEQUENCE {
     issuerDomainPolicy      CertPolicyId,
     subjectDomainPolicy     CertPolicyId }

-- subject alternative name extension OID and syntax

id-ce-subjectAltName OBJECT IDENTIFIER ::=  { id-ce 17 }

SubjectAltName ::= GeneralNames

GeneralNames ::= SEQUENCE SIZE (1..MAX) OF GeneralName

GeneralName ::= CHOICE {
     otherName                 [0]  AnotherName,
     rfc822Name                [1]  IA5String,
     dNSName                   [2]  IA5String,
     x400Address               [3]  ORAddress,
     directoryName             [4]  Name,
     ediPartyName              [5]  EDIPartyName,
     uniformResourceIdentifier [6]  IA5String,
     iPAddress                 [7]  OCTET STRING,
     registeredID              [8]  OBJECT IDENTIFIER }

-- AnotherName replaces OTHER-NAME ::= TYPE-IDENTIFIER, as
-- TYPE-IDENTIFIER is not supported in the '88 ASN.1 syntax

AnotherName ::= SEQUENCE {
     type-id    OBJECT IDENTIFIER,
     value      [0] EXPLICIT ANY DEFINED BY type-id }

EDIPartyName ::= SEQUENCE {
     nameAssigner              [0]  DirectoryString OPTIONAL,
     partyName                 [1]  DirectoryString }

-- issuer alternative name extension OID and syntax

id-ce-issuerAltName OBJECT IDENTIFIER ::=  { id-ce 18 }

IssuerAltName ::= GeneralNames

id-ce-subjectDirectoryAttributes OBJECT IDENTIFIER ::=  { id-ce 9 }

SubjectDirectoryAttributes ::= SEQUENCE SIZE (1..MAX) OF Attribute

-- basic constraints extension OID and syntax

id-ce-basicConstraints OBJECT IDENTIFIER ::=  { id-ce 19 }

BasicConstraints ::= SEQUENCE {
     cA                      BOOLEAN DEFAULT FALSE,
     pathLenConstraint       INTEGER (0..MAX) OPTIONAL }

-- name constraints extension OID and syntax

id-ce-nameConstraints OBJECT IDENTIFIER ::=  { id-ce 30 }

NameConstraints ::= SEQUENCE {
     permittedSubtrees       [0]     GeneralSubtrees OPTIONAL,
     excludedSubtrees        [1]     GeneralSubtrees OPTIONAL }

GeneralSubtrees ::= SEQUENCE SIZE (1..MAX) OF GeneralSubtree

GeneralSubtree ::= SEQUENCE {
     base                    GeneralName,
     minimum         [0]     BaseDistance DEFAULT 0,
     maximum         [1]     BaseDistance OPTIONAL }

BaseDistance ::= INTEGER (0..MAX)

-- policy constraints extension OID and syntax

id-ce-policyConstraints OBJECT IDENTIFIER ::=  { id-ce 36 }

PolicyConstraints ::= SEQUENCE {
     requireExplicitPolicy   [0]     SkipCerts OPTIONAL,
     inhibitPolicyMapping    [1]     SkipCerts OPTIONAL }

SkipCerts ::= INTEGER (0..MAX)

-- CRL distribution points extension OID and syntax

id-ce-cRLDistributionPoints     OBJECT IDENTIFIER  ::=  {id-ce 31}

CRLDistributionPoints ::= SEQUENCE SIZE (1..MAX) OF DistributionPoint

DistributionPoint ::= SEQUENCE {
     distributionPoint       [0]     DistributionPointName OPTIONAL,
     reasons                 [1]     ReasonFlags OPTIONAL,
     cRLIssuer               [2]     GeneralNames OPTIONAL }

DistributionPointName ::= CHOICE {
     fullName                [0]     GeneralNames,
     nameRelativeToCRLIssuer [1]     RelativeDistinguishedName }

ReasonFlags ::= BIT STRING {
     unused                  (0),
     keyCompromise           (1),
     cACompromise            (2),
     affiliationChanged      (3),
     superseded              (4),
     cessationOfOperation    (5),
     certificateHold         (6),
     privilegeWithdrawn      (7),
     aACompromise            (8) }

-- extended key usage extension OID and syntax

id-ce-extKeyUsage OBJECT IDENTIFIER ::= {id-ce 37}

ExtKeyUsageSyntax ::= SEQUENCE SIZE (1..MAX) OF KeyPurposeId

KeyPurposeId ::= OBJECT IDENTIFIER

-- permit unspecified key uses

anyExtendedKeyUsage OBJECT IDENTIFIER ::= { id-ce-extKeyUsage 0 }

-- extended key purpose OIDs

id-kp-serverAuth             OBJECT IDENTIFIER ::= { id-kp 1 }
id-kp-clientAuth             OBJECT IDENTIFIER ::= { id-kp 2 }
id-kp-codeSigning            OBJECT IDENTIFIER ::= { id-kp 3 }
id-kp-emailProtection        OBJECT IDENTIFIER ::= { id-kp 4 }
id-kp-timeStamping           OBJECT IDENTIFIER ::= { id-kp 8 }
id-kp-OCSPSigning            OBJECT IDENTIFIER ::= { id-kp 9 }

-- inhibit any policy OID and syntax

id-ce-inhibitAnyPolicy OBJECT IDENTIFIER ::=  { id-ce 54 }

InhibitAnyPolicy ::= SkipCerts

-- freshest (delta)CRL extension OID and syntax

id-ce-freshestCRL OBJECT IDENTIFIER ::=  { id-ce 46 }

FreshestCRL ::= CRLDistributionPoints

-- authority info access

id-pe-authorityInfoAccess OBJECT IDENTIFIER ::= { id-pe 1 }

AuthorityInfoAccessSyntax  ::=
        SEQUENCE SIZE (1..MAX) OF AccessDescription

AccessDescription  ::=  SEQUENCE {
        accessMethod          OBJECT IDENTIFIER,
        accessLocation        GeneralName  }

-- subject info access

id-pe-subjectInfoAccess OBJECT IDENTIFIER ::= { id-pe 11 }

SubjectInfoAccessSyntax  ::=
        SEQUENCE SIZE (1..MAX) OF AccessDescription

-- CRL number extension OID and syntax

id-ce-cRLNumber OBJECT IDENTIFIER ::= { id-ce 20 }

CRLNumber ::= INTEGER (0..MAX)

-- issuing distribution point extension OID and syntax

id-ce-issuingDistributionPoint OBJECT IDENTIFIER ::= { id-ce 28 }

IssuingDistributionPoint ::= SEQUENCE {
     distributionPoint          [0] DistributionPointName OPTIONAL,
     onlyContainsUserCerts      [1] BOOLEAN DEFAULT FALSE,
     onlyContainsCACerts        [2] BOOLEAN DEFAULT FALSE,
     onlySomeReasons            [3] ReasonFlags OPTIONAL,
     indirectCRL                [4] BOOLEAN DEFAULT FALSE,
     onlyContainsAttributeCerts [5] BOOLEAN DEFAULT FALSE }
     -- at most one of onlyContainsUserCerts, onlyContainsCACerts,
     -- and onlyContainsAttributeCerts may be set to TRUE.

id-ce-deltaCRLIndicator OBJECT IDENTIFIER ::= { id-ce 27 }

BaseCRLNumber ::= CRLNumber

-- reason code extension OID and syntax

id-ce-cRLReasons OBJECT IDENTIFIER ::= { id-ce 21 }

CRLReason ::= ENUMERATED {
     unspecified             (0),
     keyCompromise           (1),
     cACompromise            (2),
     affiliationChanged      (3),
     superseded              (4),
     cessationOfOperation    (5),
     certificateHold         (6),
     removeFromCRL           (8),
     privilegeWithdrawn      (9),
     aACompromise           (10) }

-- certificate issuer CRL entry extension OID and syntax

id-ce-certificateIssuer OBJECT IDENTIFIER ::= { id-ce 29 }

CertificateIssuer ::= GeneralNames

-- hold instruction extension OID and syntax

id-ce-holdInstructionCode OBJECT IDENTIFIER ::= { id-ce 23 }

HoldInstructionCode ::= OBJECT IDENTIFIER

-- ANSI x9 arc holdinstruction arc

holdInstruction OBJECT IDENTIFIER ::=
          {joint-iso-itu-t(2) member-body(2) us(840) x9cm(10040) 2}

-- ANSI X9 holdinstructions

id-holdinstruction-none OBJECT IDENTIFIER  ::=
                                      {holdInstruction 1} -- deprecated

id-holdinstruction-callissuer OBJECT IDENTIFIER ::= {holdInstruction 2}

id-holdinstruction-reject OBJECT IDENTIFIER ::= {holdInstruction 3}

-- invalidity date CRL entry extension OID and syntax

id-ce-invalidityDate OBJECT IDENTIFIER ::= { id-ce 24 }

InvalidityDate ::=  GeneralizedTime

END
'''

vscmr_conv = asn1tools.compile_string(vscmr_spec, codec="ber", numeric_enums=True)
x509_conv = asn1tools.compile_string(x509_spec, codec="der")

# Valide os certificados (verifique até a raiz, etc.) antes de adicionar nesta lista
trusted_issuers = decode_issuers([
'''
MIID+jCCA1ygAwIBAgIBAzAKBggqhkjOPQQDBDCBgjELMAkGA1UEBhMCQlIxDDAK
BgNVBAoTA1RTRTEMMAoGA1UECxMDU1RJMRMwEQYDVQQDEwpBQyBSQUlaIFVFMQsw
CQYDVQQIEwJERjERMA8GA1UEBxMIQnJhc2lsaWExIjAgBgkqhkiG9w0BCQEWE2Fj
cmFpenVlQHRzZS5qdXMuYnIwHhcNMTEwNzIyMjE1OTU3WhcNMjYwNzE4MjE1OTU3
WjB9MRAwDgYDVQQDEwdBQyBVUk5BMQswCQYDVQQIEwJERjELMAkGA1UEBhMCQlIx
IDAeBgkqhkiG9w0BCQEWEWFjdXJuYUB0c2UuanVzLmJyMQwwCgYDVQQKEwNUU0Ux
DDAKBgNVBAsTA1NUSTERMA8GA1UEBxMIQnJhc2lsaWEwgZswEAYHKoZIzj0CAQYF
K4EEACMDgYYABACBR0E65ux46ll8Z41Fi0QJf+LWQqUZDI2wIvB4+F9vHqrw0/tb
6pasdXwwxxdRia5oI21UrKsN2f0VxCtWGPAzzwHPPXSY4w9bSIg/evLDXFZ9QNKK
EWt83xzz1U6nsd2H+XI2sNSOWQS485ZYQ7nnThRgPhRsYsbCKb6Gmt7JTNudkqOC
AYIwggF+MAwGA1UdEwQFMAMBAf8wga8GA1UdIwSBpzCBpIAUhmiDe7vjSM0ZthAw
pInV395nQsChgYikgYUwgYIxCzAJBgNVBAYTAkJSMQwwCgYDVQQKEwNUU0UxDDAK
BgNVBAsTA1NUSTETMBEGA1UEAxMKQUMgUkFJWiBVRTELMAkGA1UECBMCREYxETAP
BgNVBAcTCEJyYXNpbGlhMSIwIAYJKoZIhvcNAQkBFhNhY3JhaXp1ZUB0c2UuanVz
LmJyggEBMB0GA1UdDgQWBBTNqvSylwGz8N6ytUJYWUptNK2kijALBgNVHQ8EBAMC
AYYwPAYDVR0fBDUwMzAxoC+gLYYraHR0cDovL2xjci5hY3JhaXp1ZS50c2UuanVz
LmJyL2FjcmFpenVlLmNybDBSBgNVHSAESzBJMEcGAwAAADBAMD4GCCsGAQUFBwIB
FjJodHRwOi8vZHBjLmFjcmFpenVlLnRzZS5qdXMuYnIvRFBDLVBDLUFDUkFJWlVF
LnBkZjAKBggqhkjOPQQDBAOBiwAwgYcCQS2Qnvfn3wF8Ft96nAjZH5tp/0VKwp0j
iJYy1+7Bx5PB94lW6D7Hqvfe6XClC9faSrNTQL1XFdZg2yw/hMROUb2uAkIBe8l4
fiGIaXNVTLstkxNGYoXCjJvGtBc1zeFBRXKA3S95lskEMn8QJ06vYRK6PiUu5foM
yaLcujRjcz9Mb7zpTfo=
''',
'''
MIIDITCCAoegAwIBAgIBBTAMBgorBgEEAYLcLAIBMIGCMRMwEQYDVQQDDApBQyBV
Uk5BIHYyMQswCQYDVQQIDAJERjELMAkGA1UEBhMCQlIxIjAgBgkqhkiG9w0BCQEW
E2FjdXJuYXYyQHRzZS5qdXMuYnIxDDAKBgNVBAoMA1RTRTEMMAoGA1UECwwDU1RJ
MREwDwYDVQQHDAhCcmFzaWxpYTAeFw0yMTA3MDUyMTMxNTZaFw0zNzA3MDEyMTMx
NTZaMIGBMRIwEAYDVQQDDAlBQyBVRTIwMjAxCzAJBgNVBAgMAkRGMQswCQYDVQQG
EwJCUjEiMCAGCSqGSIb3DQEJARYTYWN1ZTIwMjBAdHNlLmp1cy5icjEMMAoGA1UE
CgwDVFNFMQwwCgYDVQQLDANTVEkxETAPBgNVBAcMCEJyYXNpbGlhMFMwDAYKKwYB
BAGC3CwCAQNDAF09dk0RI6kU77+TiS2ztj0vPF03oPFKLTbUSQXMdCi5dDVRcd94
wqBk5jCzCLBhpH+wvVVxX8W2lBm/yS7VcK65AaOB8DCB7TAMBgNVHRMEBTADAQH/
MAsGA1UdDwQEAwIBBjAdBgNVHQ4EFgQUvLNUXe7qQy4egy6BKDy4rpSOQAswHwYD
VR0jBBgwFoAU0CVmxJEDSzqTE7MHSSHsqbUA4h8wPAYDVR0fBDUwMzAxoC+gLYYr
aHR0cDovL2xjci5hY3VybmF2Mi50c2UuanVzLmJyL2FjdXJuYXYyLmNybDBSBgNV
HSAESzBJMEcGAwAAADBAMD4GCCsGAQUFBwIBFjJodHRwOi8vZHBjLmFjdXJuYXYy
LnRzZS5qdXMuYnIvRFBDLVBDLUFDVVJOQVYyLlBERjAMBgorBgEEAYLcLAIBA4GF
ADuPH84sRDCnd7vmSDb3ae7qtPoUCcPbeOJb3zFtArAUm6ki7bV0Fa0jUri4LtEm
XYgrhOETAvqaRBlBk2vRH409gRbpHpMf9cvzgoMGAp+ShXHwlZ4m6XJuFXi77hMc
qGvoUFpGzp0yXzbP1uK1QMvfyVakWpczXQnvS3zay18ik9NFAA==
'''
])


if __name__ == '__main__':
    main()
