"""Support for TP-Link routers."""

import base64
from datetime import datetime
import hashlib
import logging
import re
from Crypto.PublicKey.RSA import construct
from Crypto.Cipher import PKCS1_v1_5
from Crypto.Cipher import AES
from Crypto.Cipher import PKCS1_OAEP
from Crypto.Util.Padding import pad, unpad
import binascii
import string, random
import time
import typing, hashlib, math

from aiohttp.hdrs import (
    ACCEPT,
    COOKIE,
    PRAGMA,
    REFERER,
    CONNECTION,
    KEEP_ALIVE,
    USER_AGENT,
    CONTENT_TYPE,
    CACHE_CONTROL,
    ACCEPT_ENCODING,
    ACCEPT_LANGUAGE
)
import requests
import voluptuous as vol

from homeassistant.components.device_tracker import (
    DOMAIN, PLATFORM_SCHEMA, DeviceScanner)
from homeassistant.const import (
    CONF_HOST, CONF_PASSWORD, CONF_USERNAME, HTTP_HEADER_X_REQUESTED_WITH)
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

HTTP_HEADER_NO_CACHE = 'no-cache'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_PASSWORD): cv.string,
    vol.Required(CONF_USERNAME): cv.string
})


def get_scanner(hass, config):
    """Validate the configuration and return a TP-Link scanner."""
    for cls in [WR841NTplinkDeviceScanner,
                VR600TplinkDeviceScanner,
                EAP225TplinkDeviceScanner,
                N600TplinkDeviceScanner,
                C7TplinkDeviceScanner,
                C9TplinkDeviceScanner,
                OldC9TplinkDeviceScanner,
                OriginalTplinkDeviceScanner]:
        scanner = cls(config[DOMAIN])
        if scanner.success_init:
            return scanner

    return None


class TplinkDeviceScanner(DeviceScanner):
    """Abstract parent class for all TPLink device scanners."""

    def __init__(self, config):
        """Generic scanner initialization."""
        host = config[CONF_HOST]
        username, password = config[CONF_USERNAME], config[CONF_PASSWORD]

        self.parse_macs_hyphens = re.compile('[0-9A-F]{2}-[0-9A-F]{2}-' +
                                             '[0-9A-F]{2}-[0-9A-F]{2}-' +
                                             '[0-9A-F]{2}-[0-9A-F]{2}')
        self.parse_macs_colons = re.compile('[0-9A-F]{2}:[0-9A-F]{2}:' +
                                            '[0-9A-F]{2}:[0-9A-F]{2}:' +
                                            '[0-9A-F]{2}:[0-9A-F]{2}')

        self.host = host
        self.username = username
        self.password = password

        self.last_results = {}
        self.success_init = self._update_info()

    def scan_devices(self):
        """Scan for new devices and return a list with found device IDs."""
        self._update_info()
        return self.last_results

    # pylint: disable=no-self-use
    def get_device_name(self, device):
        """Firmware doesn't save the name of the wireless device.
        Home Assistant will default to MAC address."""
        return None

    def get_base64_cookie_string(self):
        """Encode Base 64 authentication string for some scanners."""
        username_password = '{}:{}'.format(self.username, self.password)
        b64_encoded_username_password = base64.b64encode(
            username_password.encode('ascii')
        ).decode('ascii')
        return 'Authorization=Basic {}'.format(b64_encoded_username_password)


class OriginalTplinkDeviceScanner(TplinkDeviceScanner):
    """This class queries a wireless router running TP-Link firmware.
    Oldest firmware supported by this integration."""

    def _update_info(self):
        """Ensure the information from the TP-Link router is up to date.
        Return boolean if scanning successful.
        """
        _LOGGER.info("[Original] Loading wireless clients...")

        # Check 2.4GHz band
        url = 'http://{}/userRpm/WlanStationRpm.htm'.format(self.host)
        referer = 'http://{}'.format(self.host)
        page = requests.get(
            url, auth=(self.username, self.password),
            headers={REFERER: referer}, timeout=4)
        
        # Check 5Ghz band (if available)
        url = 'http://{}/userRpm/WlanStationRpm_5g.htm'.format(self.host)
        page2 = requests.get(
            url, auth=(self.username, self.password),
            headers={REFERER: referer}, timeout=4)

        result = self.parse_macs_hyphens.findall(page.text + ' ' + page2.text)

        if result:
            self.last_results = [mac.replace("-", ":") for mac in result]
            return True

        return False


class N600TplinkDeviceScanner(TplinkDeviceScanner):
    """This class queries TP-Link N600 router and TL-WR840N router."""

    def _update_info(self):
        """Ensure the information from the TP-Link router is up to date.
        Return boolean if scanning successful.
        """
        _LOGGER.info("[N600] Loading wireless clients...")

        # Router uses Authorization cookie instead of header.
        cookie = self.get_base64_cookie_string()

        referer = 'http://{}'.format(self.host)
        mac_results = []

        # Check both the 2.4GHz and 5GHz client lists.
        for clients_frequency in ('1', '2'):

            # Refresh associated clients.
            page = requests.post(
                'http://{}/cgi?7'.format(self.host),
                headers={REFERER: referer, COOKIE: cookie},
                data=(
                    '[ACT_WLAN_UPDATE_ASSOC#1,{},0,0,0,0#0,0,0,0,0,0]0,0\r\n'
                    ).format(clients_frequency),
                timeout=4)
            if not page.status_code == 200:
                _LOGGER.error("Error %s from router", page.status_code)
                return False

            # Retrieve associated clients.
            page = requests.post(
                'http://{}/cgi?6'.format(self.host),
                headers={REFERER: referer, COOKIE: cookie},
                data=(
                    '[LAN_WLAN_ASSOC_DEV#0,0,0,0,0,0#1,{},0,0,0,0]0,1\r\n'
                    'AssociatedDeviceMACAddress\r\n'
                    ).format(clients_frequency),
                timeout=4)
            if not page.status_code == 200:
                _LOGGER.error("Error %s from router", page.status_code)
                return False

            mac_results.extend(self.parse_macs_colons.findall(page.text))

        if mac_results:
            self.last_results = mac_results
            return True

        return False


class OldC9TplinkDeviceScanner(TplinkDeviceScanner):
    """This class queries a C9 router with old firmware."""

    def scan_devices(self):
        """Scan for new devices and return a list with found device IDs."""
        self._update_info()
        return self.last_results.keys()

    # pylint: disable=no-self-use
    def get_device_name(self, device):
        """Get firmware doesn't save the name of the wireless device."""
        return self.last_results.get(device)

    def _update_info(self):
        """Ensure the information from the TP-Link router is up to date.
        Return boolean if scanning successful.
        """
        _LOGGER.info("[OldC9] Loading wireless clients...")

        url = 'http://{}/data/map_access_wireless_client_grid.json' \
            .format(self.host)
        referer = 'http://{}'.format(self.host)

        # Router uses Authorization cookie instead of header
        # Let's create the cookie
        cookie = self.get_base64_cookie_string()

        response = requests.post(
            url, headers={REFERER: referer, COOKIE: cookie},
            timeout=4)

        try:
            result = response.json().get('data')
        except ValueError:
            _LOGGER.error("Router didn't respond with JSON. "
                          "Check if credentials are correct.")
            return False

        if result:
            self.last_results = {
                device['mac_addr'].replace('-', ':'): device['name']
                for device in result
                }
            return True

        return False


class C9TplinkDeviceScanner(TplinkDeviceScanner):
    """This class queries the Archer C9 router with version 150811 or high."""

    def __init__(self, config):
        """Initialize the scanner."""
        self.stok = ''
        self.sysauth = ''
        super(C9TplinkDeviceScanner, self).__init__(config)

    def scan_devices(self):
        """Scan for new devices and return a list with found device IDs."""
        self._update_info()
        self._log_out()
        return self.last_results.keys()

    def get_device_name(self, device):
        """Get the firmware doesn't save the name of the wireless device.
        We are forced to use the MAC address as name here.
        """
        return self.last_results.get(device)

    def _get_auth_tokens(self):
        """Retrieve auth tokens from the router."""
        _LOGGER.info("Retrieving auth tokens...")

        url = 'http://{}/cgi-bin/luci/;stok=/login?form=login' \
            .format(self.host)
        referer = 'http://{}/webpages/login.html'.format(self.host)

        # If possible implement RSA encryption of password here.
        response = requests.post(
            url, params={'operation': 'login', 'username': self.username,
                         'password': self.password},
            headers={REFERER: referer}, timeout=4)

        try:
            self.stok = response.json().get('data').get('stok')
            _LOGGER.info(self.stok)
            regex_result = re.search(
                'sysauth=(.*);', response.headers['set-cookie'])
            self.sysauth = regex_result.group(1)
            _LOGGER.info(self.sysauth)
            return True
        except (ValueError, KeyError, AttributeError) as _:
            _LOGGER.error("Couldn't fetch auth tokens! Response was: %s",
                          response.text)
            return False

    def _update_info(self):
        """Ensure the information from the TP-Link router is up to date.
        Return boolean if scanning successful.
        """
        _LOGGER.info("[C9] Loading wireless clients...")

        if (self.stok == '') or (self.sysauth == ''):
            self._get_auth_tokens()

        url = ('http://{}/cgi-bin/luci/;stok={}/admin/wireless?'
               'form=statistics').format(self.host, self.stok)
        referer = 'http://{}/webpages/index.html'.format(self.host)

        response = requests.post(
            url, params={'operation': 'load'}, headers={REFERER: referer},
            cookies={'sysauth': self.sysauth}, timeout=5)

        try:
            json_response = response.json()

            if json_response.get('success'):
                result = response.json().get('data')
            else:
                if json_response.get('errorcode') == 'timeout':
                    _LOGGER.info("Token timed out. Relogging on next scan")
                    self.stok = ''
                    self.sysauth = ''
                    return False
                _LOGGER.error(
                    "An unknown error happened while fetching data")
                return False
        except ValueError:
            _LOGGER.error("Router didn't respond with JSON. "
                          "Check if credentials are correct")
            return False

        if result:
            self.last_results = {
                device['mac'].replace('-', ':'): device['mac']
                for device in result
                }
            return True

        return False

    def _log_out(self):
        _LOGGER.info("Logging out of router admin interface...")

        url = ('http://{}/cgi-bin/luci/;stok={}/admin/system?'
               'form=logout').format(self.host, self.stok)
        referer = 'http://{}/webpages/index.html'.format(self.host)

        requests.post(
            url, params={'operation': 'write'}, headers={REFERER: referer},
            cookies={'sysauth': self.sysauth})
        self.stok = ''
        self.sysauth = ''


class C7TplinkDeviceScanner(TplinkDeviceScanner):
    """This class queries an Archer C7 router with TP-Link firmware 150427."""

    def __init__(self, config):
        """Initialize the scanner."""
        self.credentials = ''
        self.token = ''
        super(C7TplinkDeviceScanner, self).__init__(config)

    def _get_auth_tokens(self):
        """Retrieve auth tokens from the router."""
        _LOGGER.info("Retrieving auth tokens...")
        url = 'http://{}/userRpm/LoginRpm.htm?Save=Save'.format(self.host)

        # Generate md5 hash of password. The C7 appears to use the first 15
        # characters of the password only, so we truncate to remove additional
        # characters from being hashed.
        password = hashlib.md5(self.password.encode('utf')[:15]).hexdigest()
        credentials = '{}:{}'.format(self.username, password).encode('utf')

        # Encode the credentials to be sent as a cookie.
        self.credentials = base64.b64encode(credentials).decode('utf')

        # Create the authorization cookie.
        cookie = 'Authorization=Basic {}'.format(self.credentials)

        response = requests.get(url, headers={COOKIE: cookie})

        try:
            result = re.search(r'window.parent.location.href = '
                               r'"https?:\/\/.*\/(.*)\/userRpm\/Index.htm";',
                               response.text)
            if not result:
                return False
            self.token = result.group(1)
            return True
        except ValueError:
            _LOGGER.error("Couldn't fetch auth tokens")
            return False

    def _update_info(self):
        """Ensure the information from the TP-Link router is up to date.
        Return boolean if scanning successful.
        """
        _LOGGER.info("[C7] Loading wireless clients...")

        if (self.credentials == '') or (self.token == ''):
            self._get_auth_tokens()

        mac_results = []

        # Check both the 2.4GHz and 5GHz client list URLs
        for clients_url in ('WlanStationRpm.htm', 'WlanStationRpm_5g.htm'):
            url = 'http://{}/{}/userRpm/{}' \
                .format(self.host, self.token, clients_url)
            referer = 'http://{}'.format(self.host)
            cookie = 'Authorization=Basic {}'.format(self.credentials)

            page = requests.get(url, headers={
                COOKIE: cookie,
                REFERER: referer,
            })
            mac_results.extend(self.parse_macs_hyphens.findall(page.text))

        if not mac_results:
            return False

        self.last_results = [mac.replace("-", ":") for mac in mac_results]
        return True


class EAP225TplinkDeviceScanner(TplinkDeviceScanner):
    """This class queries a TP-Link EAP-225 AP with newer TP-Link FW."""

    def scan_devices(self):
        """Scan for new devices and return a list with found MAC IDs."""
        self._update_info()
        return self.last_results.keys()

    def _update_info(self):
        """Ensure the information from the TP-Link AP is up to date.
        Return boolean if scanning successful.
        """
        _LOGGER.info("[EAP225] Loading wireless clients...")

        base_url = 'http://{}'.format(self.host)

        header = {
            USER_AGENT:
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.12;"
                " rv:53.0) Gecko/20100101 Firefox/53.0",
            ACCEPT: "application/json, text/javascript, */*; q=0.01",
            ACCEPT_LANGUAGE: "Accept-Language: en-US,en;q=0.5",
            ACCEPT_ENCODING: "gzip, deflate",
            CONTENT_TYPE: "application/x-www-form-urlencoded; charset=UTF-8",
            HTTP_HEADER_X_REQUESTED_WITH: "XMLHttpRequest",
            REFERER: "http://{}/".format(self.host),
            CONNECTION: KEEP_ALIVE,
            PRAGMA: HTTP_HEADER_NO_CACHE,
            CACHE_CONTROL: HTTP_HEADER_NO_CACHE,
        }

        password_md5 = hashlib.md5(
            self.password.encode('utf')).hexdigest().upper()

        # Create a session to handle cookie easier
        session = requests.session()
        session.get(base_url, headers=header)

        login_data = {"username": self.username, "password": password_md5}
        session.post(base_url, login_data, headers=header)

        # A timestamp is required to be sent as get parameter
        timestamp = int(datetime.now().timestamp() * 1e3)

        client_list_url = '{}/data/monitor.client.client.json'.format(
            base_url)

        get_params = {
            'operation': 'load',
            '_': timestamp,
        }

        response = session.get(
            client_list_url, headers=header, params=get_params)
        session.close()
        try:
            list_of_devices = response.json()
        except ValueError:
            _LOGGER.error("AP didn't respond with JSON. "
                          "Check if credentials are correct")
            return False

        if list_of_devices:
            self.last_results = {
                device['MAC'].replace('-', ':'): device['DeviceName']
                for device in list_of_devices['data']
                }
            return True

        return False

class VR600TplinkDeviceScanner(TplinkDeviceScanner):
    """This class queries an Archer VR600 router with TP-Link firmware."""

    def __init__(self, config):
        """Initialize the scanner."""
        self.pubkey = ''
        self.jsessionId = ''
        self.token = ''
        super(VR600TplinkDeviceScanner, self).__init__(config)

    def _get_pub_key(self):
        # Get the modulu and exponent from the router
        url = 'http://{}/cgi/getParm'.format(self.host)
        referer = 'http://{}'.format(self.host)
        response = requests.post(url, headers={ 'REFERER': referer})
        if not response.status_code == 200:
            return False

        ee = self._get_field_from_router_response(response.text, 'ee')
        nn = self._get_field_from_router_response(response.text, 'nn')

        try:
            e = int(ee, 16)
            n = int(nn, 16)  #snipped for brevity
        except ValueError:
            return False

        pubkey = construct((n, e))
        self.pubkey = PKCS1_v1_5.new(pubkey)

        return True

    def _get_field_from_router_response(self, rText, key):
        lines = rText.split('\n')
        for line in lines:
            if (line.startswith('var '+ key)):
                # var ee="010101" => "010101"
                return line.split('=\"')[1].split('\";')[0]
        return ''

    def _get_jsession_id(self):
        username = self.username.encode('utf-8')
        password = self.password.encode('utf-8')

        b64pass = base64.b64encode(password)
        encryptedUsername = self.pubkey.encrypt(username)
        encryptedPassword = self.pubkey.encrypt(b64pass)

        base16username = base64.b16encode(encryptedUsername).decode('utf-8').lower()
        base16password = base64.b16encode(encryptedPassword).decode('utf-8').lower()

        referer = 'http://{}'.format(self.host)
        url = 'http://{}/cgi/login?UserName={}&Passwd={}&Action=1&LoginStatus=0'.format(self.host, base16username, base16password)

        randomSessionNum = self._get_random_alphaNumeric_string(15)

        headers= { REFERER: referer }

        response = requests.post(url, headers=headers)

        if not response.status_code == 200:
            _LOGGER.error("Error %s from router", response.text)
            return False

        self.jsessionId = dict(response.cookies)['JSESSIONID']
        return True

    def _get_random_alphaNumeric_string(self, stringLength=15):
        lettersAndDigits = string.ascii_letters + string.digits
        return ''.join((random.choice(lettersAndDigits) for i in range(stringLength)))

    def _get_token(self):
        url = 'http://{}/'.format(self.host)
        referer = 'http://{}'.format(self.host)

        headers= {
            REFERER: referer,
            COOKIE: 'loginErrorShow=1; JSESSIONID='+self.jsessionId,
            }

        cookies = {'JSESSIONID': self.jsessionId}
        response = requests.get(url, headers=headers, cookies=cookies)

        if not response.status_code == 200:
            _LOGGER.error("Error %s from router", response.text)
            return False

        split = response.text.index('var token=') + len('var token=\"') 
        token = response.text[split:split+30]

        self.token = token
        return True

    def _get_auth_tokens(self):
        """Retrieve auth tokens from the router."""

        _LOGGER.info("Retrieving PublicKey...")
        pubkey = self._get_pub_key()
        if not pubkey:
            return False

        _LOGGER.info("Retrieving JSessionID...")
        jsessionId = self._get_jsession_id()
        if not jsessionId:
            return False

        _LOGGER.info("Retrieving Token...")
        token = self._get_token()
        if not token:
            return False

        return True

    def _get_mac_results(self):
        referer = 'http://{}'.format(self.host)
        headers= {
            'TokenID': self.token,
            REFERER: referer,
            COOKIE: 'JSESSIONID=' + self.jsessionId
            }

        mac_results = []

        # Check both the 2.4GHz and 5GHz client lists.
        for clients_frequency in ('1', '2'):

            # Refresh associated clients.
            page = requests.post(
                'http://{}/cgi?7'.format(self.host),
                headers=headers,
                data=(
                    '[ACT_WLAN_UPDATE_ASSOC#1,{},0,0,0,0#0,0,0,0,0,0]0,0\r\n'
                    ).format(clients_frequency),
                timeout=4)
            if not page.status_code == 200:
                _LOGGER.error("Error %s from router", page.status_code)
                return False

            # Retrieve associated clients.
            page = requests.post(
                'http://{}/cgi?6'.format(self.host),
                headers=headers,
                data=(
                    '[LAN_WLAN_ASSOC_DEV#0,0,0,0,0,0#1,{},0,0,0,0]0,1\r\n'
                    'AssociatedDeviceMACAddress\r\n'
                    ).format(clients_frequency),
                timeout=4)
            if not page.status_code == 200:
                _LOGGER.error("Error %s from router", page.status_code)
                return False

            mac_results.extend(self.parse_macs_colons.findall(page.text))
    
        self.last_results = mac_results
        return True

    def _update_info(self):
        """Ensure the information from the TP-Link router is up to date.
        Return boolean if scanning successful.
        """
        _LOGGER.info("[VR600] Loading wireless clients...")

        if (self.jsessionId == '') or (self.token == ''):
            gotToken = self._get_auth_tokens()
            if not gotToken:
                # Retry
                _LOGGER.info("Failed to get AuthTokens. Retrying in 3 secs.")
                time.sleep(3)
                gotToken = self._get_auth_tokens()
        else:
            gotToken = True

        if not gotToken:
            """ In case of failure - force re-login """
            self.jsessionId = ''
            self.token = ''
            return False

        macResults = self._get_mac_results()
        if not macResults:
            """ In case of failure - force re-login """
            self.jsessionId = ''
            self.token = ''
            return False
        return True
   
class WR841NTplinkDeviceScanner(TplinkDeviceScanner):
    """This class queries an TL-WR841N router with TP-Link firmware."""
    """Adopted from https://github.com/0xf15h/tp_link_gdpr """

    def __init__(self, config):
        """Initialize the scanner."""
        self.BLOCK_SIZE: int = 16
        self.AES_KEY: str = (str(int(time.time() * 1000)) + "" + str(random.random() * 1000000000))[0:self.BLOCK_SIZE]
        self.AES_IV: str = (str(int(time.time() * 1000)) + "" + str(random.random() * 1000000000))[0:self.BLOCK_SIZE]
        self.e: int
        self.n: int
        self.seq: int
        self.pubkey = ''
        self.jsessionId = ''
        self.token = ''
        super(WR841NTplinkDeviceScanner, self).__init__(config)

    def _get_pub_key(self) -> typing.Union[typing.Tuple[int, int, int], None]:
        # Requests the public key and sequence from the router.
        url = 'http://{}/cgi?8'.format(self.host)
        referer = 'http://{}'.format(self.host)
        data = "[/cgi/getParm#0,0,0,0,0,0#0,0,0,0,0,0]0,0\r\n"

        response = requests.post(url, headers={ 'Referer': referer}, data=data)
        if not response.status_code == 200:
            return False

        ee = self._get_field_from_router_response(response.text, 'ee')
        nn = self._get_field_from_router_response(response.text, 'nn')
        seq = self._get_field_from_router_response(response.text, 'seq')

        _LOGGER.debug(f"[+] RSA n: {nn}")
        _LOGGER.debug(f"[+] RSA e: {ee}")
        _LOGGER.debug(f"[+] Sequence: {seq}")

        try:
            e = int(ee, 16)
            n = int(nn, 16)  #snipped for brevity
            seq = int(seq, 10)
        except ValueError:
            return None

        return e, n, seq

    def _get_field_from_router_response(self, rText, key):
        lines = rText.split('\n')
        for line in lines:
            if (line.startswith('var '+ key)):
                # var ee="010101" => "010101"
                return line.split('=\"')[1].split('\";')[0]
        return ''

    def _get_jsession_id(self):

        # Get the RSA public key parameters and the sequence
        #rsa_vals = self._get_pub_key(s)
        #if rsa_vals is None:
        #    _LOGGER.error("[-] Failed to get RSA public key and sequence values")
        #    return False

        # Create the data field
        login_data = f"8\r\n[/cgi/login#0,0,0,0,0,0#0,0,0,0,0,0]0,2\r\nusername={self.username}\r\npassword={self.password}\r\n"
        data_ciphertext = self._aes_encrypt(login_data.encode())
        data = base64.b64encode(data_ciphertext).decode()

         # Create the sign field
        sign = self._rsa_encrypt(self.e, self.n, self.seq, data)

        # Send the authentication request
        url = 'http://{}/cgi_gdpr'.format(self.host)

        referer = 'http://{}'.format(self.host)
        headers= { "Referer": referer }

        request_data = f"sign={sign.hex()}\r\ndata={data}\r\n"

        response = requests.post(url, headers=headers, data=request_data)
        
        if not response.status_code == 200:
            _LOGGER.error("Error %s from router", response.text)
            return False
        
        self.jsessionId = dict(response.cookies)['JSESSIONID']

        decrypted_response = self._aes_decrypt(response.text)
        _LOGGER.debug(decrypted_response)

        return True

    def _pad(self, s):
        return s + (self.BLOCK_SIZE - len(s) % self.BLOCK_SIZE) * chr(self.BLOCK_SIZE - len(s) % self.BLOCK_SIZE)

    def _aes_encrypt(self, plaintext: bytes) -> bytes:
        aes_key = self.AES_KEY.encode("utf-8")
        aes_iv = self.AES_IV.encode("utf-8")
        pad_plaintext = pad(plaintext, self.BLOCK_SIZE)
        cipher = AES.new(aes_key, AES.MODE_CBC, aes_iv)
        ciphertext = cipher.encrypt(pad_plaintext)
        return ciphertext

    def _aes_decrypt(self, response_text: str) -> bytes:
         # Decode the Base64 encoded response
        decoded: bytes = base64.b64decode(response_text)

        aes_key = self.AES_KEY.encode("utf-8")
        aes_iv = self.AES_IV.encode("utf-8")
        pad_plaintext = pad(decoded, self.BLOCK_SIZE)
        cipher = AES.new(aes_key, AES.MODE_CBC, aes_iv)
        decrypted_resp = cipher.decrypt(decoded)

        # Remove the PKCS #7 padding
        num_padding_bytes = int(decrypted_resp[-1])
        decrypted_resp = decrypted_resp[:-num_padding_bytes]

        decrypted_resp_str: str = decrypted_resp.decode()

        return decrypted_resp_str

    def _rsa_encrypt(self, e: int, n: int, seq: int, data: bytes) -> bytes:
        """
        RSA encrypts plaintext. TP-Link breaks the plaintext down into 64 byte blocks and concatenates the output.
        :param e: The RSA public key's e value
        :param n: The RSA public key's n value
        :param seq: Sequence value + data length
        :param plaintext: The data to encrypt
        :return: RSA encrypted ciphertext
        """
        seq_with_data_len = seq + len(data)
        auth_hash = hashlib.md5(f"{self.username}{self.password}".encode()).digest()
        # The string __must__ be null terminated, otherwise strlen gets the wrong size
        plaintext = f"key={self.AES_KEY}&iv={self.AES_IV}&h={auth_hash.hex()}&s={seq_with_data_len}\x00\r\n".encode()
    
        rsa_block_size = 64  # This is set by the router
        # Align the input with the block size since PKCS1 padding is not used
        if len(plaintext) % rsa_block_size != 0:
            plaintext += b"\x00" * (rsa_block_size - (len(plaintext) % rsa_block_size))
        num_blocks = int(len(plaintext) / rsa_block_size)
        ciphertext = bytes()
        block_start = 1
        block_end = rsa_block_size
        for block_itr in range(num_blocks):
            # RSA encrypt manually because the cryptography package does not allow RSA without padding because it's unsafe
            plaintext_num = int.from_bytes(plaintext[block_start - 1:block_end], byteorder="big")
            ciphertext_num = pow(plaintext_num, e, n)
            ciphertext += ciphertext_num.to_bytes(math.ceil(n.bit_length() / 8), byteorder="big")
            block_start += rsa_block_size
            block_end += rsa_block_size
        return ciphertext

    def _get_auth_tokens(self):
        """Retrieve auth tokens from the router."""

        _LOGGER.info("Retrieving RSA public key parameters and the sequence...")
        rsa_vals = self._get_pub_key()
        if not rsa_vals:
            return False
        self.e, self.n, self.seq = rsa_vals

        _LOGGER.info("Retrieving JSessionID...")
        jsessionId = self._get_jsession_id()
        if not jsessionId:
            return False

        return True

    def _get_mac_results(self):
        url = 'http://{}/cgi_gdpr'.format(self.host)
        referer = 'http://{}/'.format(self.host)
        headers = { "Referer": referer }
        cookies =  { "JSESSIONID": self.jsessionId }

        mac_results = []

        # Check both the 2.4GHz and 5GHz client lists.
        for clients_frequency in ('1', '2'):
            
            request_string = f"7\r\n[ACT_WLAN_UPDATE_ASSOC#1,{clients_frequency},0,0,0,0#0,0,0,0,0,0]0,0\r\n"
            data_ciphertext = self._aes_encrypt(request_string.encode())
            data = base64.b64encode(data_ciphertext).decode()
            sign = self._rsa_encrypt(self.e, self.n, self.seq, data)
            
            request_data = f"sign={sign.hex()}\r\ndata={data}\r\n"

            page = requests.post(url, headers=headers, cookies=cookies, data=request_data, timeout=4)
            if not page.status_code == 200:
                _LOGGER.error("Error %s from router", page.status_code)
                return False
            
            decrypted_response = self._aes_decrypt(page.text)
            _LOGGER.debug(decrypted_response)

            # Retrieve associated clients.
            request_string = f"6\r\n[LAN_WLAN_ASSOC_DEV#0,0,0,0,0,0#1,{clients_frequency},0,0,0,0]0,1\r\nAssociatedDeviceMACAddress\r\n"
            data_ciphertext = self._aes_encrypt(request_string.encode())
            data = base64.b64encode(data_ciphertext).decode()
            sign = self._rsa_encrypt(self.e, self.n, self.seq, data)

            request_data = f"sign={sign.hex()}\r\ndata={data}\r\n"

            page = requests.post(url, headers=headers, cookies=cookies, data=request_data, timeout=4)

            if not page.status_code == 200:
                _LOGGER.error("Error %s from router", page.status_code)
                return False

            decrypted_response = self._aes_decrypt(page.text)
            _LOGGER.debug(decrypted_response)

            mac_results.extend(self.parse_macs_colons.findall(decrypted_response))
    
        self.last_results = mac_results
        return True

    def _update_info(self):
        """Ensure the information from the TP-Link router is up to date.
        Return boolean if scanning successful.
        """
        _LOGGER.info("[WR841N] Loading wireless clients...")

        if (self.jsessionId == '') or (self.token == ''):
            gotToken = self._get_auth_tokens()
            if not gotToken:
                # Retry
                _LOGGER.info("Failed to get AuthTokens. Retrying in 3 secs.")
                time.sleep(3)
                gotToken = self._get_auth_tokens()
        else:
            gotToken = True

        if not gotToken:
            """ In case of failure - force re-login """
            self.jsessionId = ''
            self.token = ''
            return False

        macResults = self._get_mac_results()
        if not macResults:
            """ In case of failure - force re-login """
            self.jsessionId = ''
            self.token = ''
            return False
        return True
