import sys
import types


class _FakeAESObject:
    def encrypt(self, data: bytes) -> bytes:
        return data

    def decrypt(self, data: bytes) -> bytes:
        return data


class _FakeAESModule:
    MODE_ECB = 1

    @staticmethod
    def new(key, mode):
        return _FakeAESObject()


def install_crypto_stub():
    crypto_mod = types.ModuleType("Crypto")
    cipher_mod = types.ModuleType("Crypto.Cipher")
    cipher_mod.AES = _FakeAESModule
    crypto_mod.Cipher = cipher_mod
    sys.modules.setdefault("Crypto", crypto_mod)
    sys.modules.setdefault("Crypto.Cipher", cipher_mod)
