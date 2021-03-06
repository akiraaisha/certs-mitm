

# Copyright (C) 2014       Alvaro Felipe Melchor (alvaro.felipe91@gmail.com)


# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


from pyasn1.codec.der import decoder, encoder
from pyasn1_modules import rfc2560, rfc2459
from pyasn1.type import univ
import hashlib
import M2Crypto.X509
from M2Crypto.X509 import FORMAT_DER
from notification.event_notification import MITMNotification
# from M2Crypto import BIO, RSA, EVP
# from pyasn1.type.useful import GeneralizedTime

# All the code was extracted from  bit.ly/1mxntVN

import re
import urllib2

sha1oid = univ.ObjectIdentifier((1, 3, 14, 3, 2, 26))
sha1WithRSAEncryption = rfc2459.sha1WithRSAEncryption
sha256WithRSAEncryption = univ.ObjectIdentifier((1, 2, 840, 113549, 1, 1, 11))


class ValueOnlyBitStringEncoder(encoder.encoder.BitStringEncoder):
        # These methods just do not encode tag and legnth fields of TLV
        def encodeTag(self, *args):
            return ''

        def encodeLength(self, *args):
            return ''

        def encodeValue(*args):
            (
                substrate,
                isConstructed
            ) = encoder.encoder.BitStringEncoder.encodeValue(*args)
            # encoded bit-string value
            return substrate[1:], isConstructed

        def __call__(self, bitStringValue):
            return self.encode(None, bitStringValue, defMode=1, maxChunkSize=0)


class Ocsp:
    """
    All the things related with ocsp
    """

    def __init__(self, cert):
        self.issuer_cert = cert.der_data(1)
        self._name = cert.ca_name()
        self.user_cert = cert.der_data()
        self._extract_ocsp_uri()
        self.valueOnlyBitStringEncoder = ValueOnlyBitStringEncoder()
        self.tbsResponseData = None
        (
            self.status,
            self.certID,
            self.thisUpdate,
            self.nextUpdate,
            self.issuerHashz
        ) = self._check_ocsp()

    def get_response(self):
        return (
            self.status,
            self.certID,
            self.thisUpdate,
            self.nextUpdate,
            self.issuerHashz
            )

    def _extract_ocsp_uri(self):
        try:
            cert = M2Crypto.X509.load_cert_string(self.user_cert, FORMAT_DER)
        except:
            self.ocsp_url = None
            return
        certificateExtensions = {}

        for index in range(cert.get_ext_count()):
            ext = cert.get_ext_at(index)
            certificateExtensions[ext.get_name()] = ext.get_value()
        try:
            infos = [
                x.strip() for x in
                certificateExtensions["authorityInfoAccess"].split('\n')
                ]
        except KeyError:
            self.ocsp_url = None
            return
        ocsp_url = None
        for info in infos:
            if re.match(r"^OCSP - URI:", info):
                ocsp_url = info.replace("OCSP - URI:", "")
                break
        self.ocsp_url = ocsp_url

    def parse_tbsResponse(self, tbsResponse):
        response = self.tbsResponseData.getComponentByName(
            'responses').getComponentByPosition(0)
        certStatus = response.getComponentByName('certStatus').getName()
        certId = response.getComponentByName('certID').getComponentByName(
            'serialNumber')
        issuerKeyHash = response.getComponentByName(
            'certID').getComponentByName('issuerKeyHash')
        thisUpdate = response.getComponentByName('thisUpdate')
        nextUpdate = response.getComponentByName('nextUpdate')
        # hashAlgorithm = response.getComponentByName(
        # 'certID').getComponentByName(
        # 'hashAlgorithm').getComponentByPosition(0)
        return (str(certStatus), certId,
                str(thisUpdate), str(nextUpdate), issuerKeyHash)

    def check_certificate_transparency(self):
        if self.tbsResponseData is None:
            return
        response = self.tbsResponseData.getComponentByName(
            'responses').getComponentByPosition(0)
        extensions = response.getComponentByName('singleExtensions')
        ctoid = univ.ObjectIdentifier((1, 3, 6, 1, 4, 1, 11129, 2, 4, 5))
        sct = None
        if extensions is None:
            return sct
        for extension in extensions:
            oid = extension.getComponentByPosition(0)
            if oid == ctoid:
                sct = str(extension.getComponentByPosition(2)).encode('hex')
        return sct

    def _check_ocsp(self):
        self._get_ocsp_response()
        if self.tbsResponseData is None:
            return (None, None, None, None, None)
        if self.tbsResponseData == -1:
            return (3, None, None, None, None)
        return self.parse_tbsResponse(self.tbsResponseData)

    def make_ocsp_request(self, issuerCert, userCert):
        issuerTbsCertificate = issuerCert.getComponentByName('tbsCertificate')
        # issuerSubject = issuerTbsCertificate.getComponentByName('subject')

        userTbsCertificate = userCert.getComponentByName('tbsCertificate')
        userIssuer = userTbsCertificate.getComponentByName('issuer')
        userIssuerHash = hashlib.sha1(
            encoder.encode(userIssuer)
            ).digest()

        issuerSubjectPublicKey = issuerTbsCertificate.getComponentByName(
            'subjectPublicKeyInfo').getComponentByName('subjectPublicKey')

        issuerKeyHash = hashlib.sha1(
            self.valueOnlyBitStringEncoder(issuerSubjectPublicKey)
            ).digest()

        userSerialNumber = userTbsCertificate.getComponentByName(
            'serialNumber')
        # Build request object

        request = rfc2560.Request()

        reqCert = request.setComponentByName('reqCert').getComponentByName(
            'reqCert')

        hashAlgorithm = reqCert.setComponentByName(
            'hashAlgorithm').getComponentByName('hashAlgorithm')
        hashAlgorithm.setComponentByName('algorithm', sha1oid)

        reqCert.setComponentByName('issuerNameHash', userIssuerHash)
        reqCert.setComponentByName('issuerKeyHash', issuerKeyHash)
        reqCert.setComponentByName('serialNumber', userSerialNumber)

        ocspRequest = rfc2560.OCSPRequest()

        tbsRequest = ocspRequest.setComponentByName(
            'tbsRequest').getComponentByName('tbsRequest')
        tbsRequest.setComponentByName('version', 'v1')

        requestList = tbsRequest.setComponentByName(
            'requestList').getComponentByName('requestList')
        requestList.setComponentByPosition(0, request)

        return ocspRequest

    def _get_ocsp_response(self):
        if self.ocsp_url is not None:
            try:
                issuerCert, _ = decoder.decode(
                    self.issuer_cert, asn1Spec=rfc2459.Certificate())
                userCert, _ = decoder.decode(
                    self.user_cert, asn1Spec=rfc2459.Certificate())

            except:
                return

            ocspReq = self.make_ocsp_request(issuerCert, userCert)

            try:
                httpReq = urllib2.Request(
                    self.ocsp_url,
                    encoder.encode(ocspReq),
                    {'Content-Type': 'application/ocsp-request'}
                    )
                httpRsp = urllib2.urlopen(httpReq).read()
            except:
                return

# Process OCSP response

            ocspRsp, _ = decoder.decode(
                httpRsp, asn1Spec=rfc2560.OCSPResponse())
            responseStatus = ocspRsp.getComponentByName('responseStatus')
            responseBytes = ocspRsp.getComponentByName('responseBytes')
            if responseStatus == 0:
                try:
                    response = responseBytes.getComponentByName('response')
                except:
                    self.tbsResponseData = -1
                    return

                basicOCSPResponse, _ = decoder.decode(
                    response, asn1Spec=rfc2560.BasicOCSPResponse()
                    )

                self.tbsResponseData = basicOCSPResponse.getComponentByName(
                    'tbsResponseData')

            else:
                mes = "get status %d %s" % (
                    responseStatus, self._name)
                MITMNotification.notify(title="OCSP", message=mes)

