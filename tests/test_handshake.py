import unittest
from copy import deepcopy

from mcp_exit_test.handshake import SUPPORTED_PROTOCOLS, valid_handshake


def request(version):
    return {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }


class HandshakeTests(unittest.TestCase):
    def test_required_request_members_and_types(self):
        for version in SUPPORTED_PROTOCOLS:
            base = request(version)
            for scope in [(), ("params",), ("params", "clientInfo")]:
                obj = base
                for key in scope:
                    obj = obj[key]
                for member in obj:
                    for remove in [True, False]:
                        with self.subTest(
                            version=version, scope=scope, member=member, remove=remove
                        ):
                            changed = deepcopy(base)
                            target = changed
                            for key in scope:
                                target = target[key]
                            if remove:
                                del target[member]
                            else:
                                target[member] = None
                            self.assertFalse(valid_handshake(changed, version, "initialize"))

    def test_message_roles_and_request_ids(self):
        for version in SUPPORTED_PROTOCOLS:
            base = request(version)
            for changes in (
                {"id": True},
                {"id": 1.5},
                {"id": []},
                {"jsonrpc": "1.0"},
                {"method": "initialize2"},
                {"error": {}},
                {"result": None},
            ):
                with self.subTest(version=version, changes=changes):
                    self.assertFalse(valid_handshake({**base, **changes}, version, "initialize"))
            for value in [0, -1, "one", "", 1.0]:
                self.assertTrue(valid_handshake({**base, "id": value}, version, "initialize"))
            notice = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            for changes in (
                {"id": None},
                {"id": 0},
                {"error": None},
                {"result": {}},
                {"jsonrpc": 2},
                {"method": "initialize"},
                {"params": []},
                {"params": None},
                {"params": 42},
                {"params": {"_meta": []}},
            ):
                with self.subTest(version=version, changes=changes):
                    self.assertFalse(
                        valid_handshake({**notice, **changes}, version, notice["method"])
                    )
            for params in ({}, {"_meta": {"progressToken": []}, "vendor/extension": False}):
                self.assertTrue(
                    valid_handshake({**notice, "params": params}, version, notice["method"])
                )
            self.assertTrue(valid_handshake(notice, version, notice["method"]))

    def test_capabilities_and_request_metadata(self):
        for version in SUPPORTED_PROTOCOLS:
            msg = request(version)
            for fields in (
                {"capabilities": []},
                {"capabilities": {"roots": []}},
                {"capabilities": {"roots": {"listChanged": 1}}},
                {"capabilities": {"experimental": {"vendor/feature": False}}},
                {"_meta": None},
                {"_meta": {"progressToken": True}},
                {"_meta": {"progressToken": 1.5}},
                {"clientInfo": {"name": "fixture", "version": "1", "title": False}},
            ):
                with self.subTest(version=version, fields=fields):
                    self.assertFalse(
                        valid_handshake(
                            {**msg, "params": {**msg["params"], **fields}}, version, "initialize"
                        )
                    )
            msg["params"]["capabilities"] = {"experimental": {"vendor/feature": {"detail": None}}}
            msg["params"]["_meta"] = {"progressToken": 2, "vendor/feature": [None]}
            self.assertTrue(valid_handshake(msg, version, "initialize"))

    def test_known_november_fields_and_june_extensions(self):
        fields = [
            ("clientInfo", {"icons": [{"mimeType": "image/png"}]}),
            ("clientInfo", {"icons": [{"src": "/relative"}]}),
            ("clientInfo", {"icons": [{"src": "urn:test:icon", "theme": "blue"}]}),
            ("clientInfo", {"icons": [{"src": "urn:test:icon", "sizes": [32]}]}),
            ("clientInfo", {"description": 17, "websiteUrl": False}),
            ("capabilities", {"elicitation": {"url": False}}),
            ("capabilities", {"sampling": {"tools": []}}),
            ("capabilities", {"tasks": {"requests": {"sampling": {"createMessage": 1}}}}),
        ]
        for version in SUPPORTED_PROTOCOLS:
            for group, content in fields:
                with self.subTest(version=version, content=content):
                    msg = request(version)
                    msg["params"][group].update(content)
                    self.assertEqual(
                        valid_handshake(msg, version, "initialize"), version == "2025-06-18"
                    )
