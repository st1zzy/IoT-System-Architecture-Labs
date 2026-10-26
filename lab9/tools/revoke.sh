#!/bin/sh
# Отзыв сертификата устройства и выпуск новой CRL: sh /tools/revoke.sh dev-003
set -eu
openssl ca -config /pki/openssl.cnf -name int_ca -revoke /pki/certs/$1.crt -crl_reason keyCompromise
openssl ca -config /pki/openssl.cnf -name int_ca -gencrl -out /pki/crl.pem
chmod 644 /pki/crl.pem
