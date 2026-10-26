#!/bin/sh
# PKI Lab 9 на OpenSSL (EC P-256): Root CA (pathlen 1, «офлайн») → Intermediate CA (pathlen 0) → листовые сертификаты.
# Выпускаются: broker (SAN lab9-broker), dev-001..003, svc-ingest; для атак — rogue (свой самоподписанный CA)
# и dev-expired (срок истёк). Повторный запуск ничего не перезаписывает.
#   docker compose run --rm pki
set -eu
P=/pki
[ -f $P/chain.pem ] && { echo "PKI уже создана"; exit 0; }
mkdir -p $P/root $P/int $P/certs
for d in root int; do mkdir -p $P/$d/newcerts; : > $P/$d/index.txt; echo 1000 > $P/$d/serial; echo 1000 > $P/$d/crlnumber; done

cat > $P/openssl.cnf <<'CNF'
[ ca ]
default_ca = int_ca
[ root_ca ]
dir = /pki/root
database = $dir/index.txt
new_certs_dir = $dir/newcerts
serial = $dir/serial
crlnumber = $dir/crlnumber
certificate = $dir/root.crt
private_key = $dir/root.key
default_md = sha256
default_days = 3650
default_crl_days = 30
policy = policy_any
unique_subject = no
copy_extensions = none
[ int_ca ]
dir = /pki/int
database = $dir/index.txt
new_certs_dir = $dir/newcerts
serial = $dir/serial
crlnumber = $dir/crlnumber
certificate = $dir/int.crt
private_key = $dir/int.key
default_md = sha256
default_days = 365
default_crl_days = 1
policy = policy_any
unique_subject = no
copy_extensions = copy
[ policy_any ]
commonName = supplied
organizationName = optional
[ req ]
distinguished_name = dn
prompt = no
[ dn ]
CN = placeholder
[ v3_root ]
basicConstraints = critical, CA:true, pathlen:1
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
[ v3_int ]
basicConstraints = critical, CA:true, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
[ v3_server ]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = serverAuth
subjectAltName = DNS:lab9-broker, DNS:localhost
authorityKeyIdentifier = keyid
[ v3_client ]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = clientAuth
authorityKeyIdentifier = keyid
CNF
C=$P/openssl.cnf
key() { openssl ecparam -name prime256v1 -genkey -noout -out "$1"; chmod 600 "$1"; }

key $P/root/root.key
openssl req -config $C -new -x509 -key $P/root/root.key -subj "/O=ENU IoT Lab/CN=ENU IoT Lab Root CA" \
  -days 3650 -sha256 -extensions v3_root -out $P/root/root.crt
key $P/int/int.key
openssl req -config $C -new -key $P/int/int.key -subj "/O=ENU IoT Lab/CN=ENU IoT Lab Devices CA" -out $P/int/int.csr
openssl ca -batch -config $C -name root_ca -extensions v3_int -days 1825 -notext -in $P/int/int.csr -out $P/int/int.crt
cat $P/int/int.crt $P/root/root.crt > $P/chain.pem

leaf() {   # имя профиль [доп. аргументы openssl ca]
  n=$1; prof=$2; shift 2
  key $P/certs/$n.key
  openssl req -config $C -new -key $P/certs/$n.key -subj "/O=ENU IoT Lab/CN=$n" -out $P/certs/$n.csr
  openssl ca -batch -config $C -name int_ca -extensions $prof -notext "$@" -in $P/certs/$n.csr -out $P/certs/$n.crt
}
leaf broker v3_server -days 365
for d in dev-001 dev-002 dev-003 svc-ingest; do leaf $d v3_client -days 365; done
leaf dev-expired v3_client -startdate 20250101000000Z -enddate 20250201000000Z

# злоумышленник: собственный CA и сертификат с CN законного устройства
key $P/rogue-ca.key
openssl req -config $C -new -x509 -key $P/rogue-ca.key -subj "/CN=Rogue CA" -days 365 -extensions v3_root -out $P/rogue-ca.crt
key $P/certs/rogue.key
openssl req -config $C -new -key $P/certs/rogue.key -subj "/CN=dev-001" -out $P/certs/rogue.csr
openssl x509 -req -in $P/certs/rogue.csr -CA $P/rogue-ca.crt -CAkey $P/rogue-ca.key -CAcreateserial -days 365 \
  -extfile $C -extensions v3_client -out $P/certs/rogue.crt

openssl ca -config $C -name int_ca -gencrl -out $P/crl.pem
chmod 644 $P/certs/*.crt $P/*.pem $P/root/root.crt $P/int/int.crt $P/certs/broker.key
chown 1883:1883 $P/certs/broker.key 2>/dev/null || true
echo "PKI: root → intermediate → broker, dev-001..003, svc-ingest, dev-expired; rogue (чужой CA)"
