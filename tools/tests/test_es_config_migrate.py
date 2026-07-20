"""Tests for es_config_migrate_yaml.py — zmeventnotification.ini / secrets.ini -> YAML.

migrate_es_config: rewrites {{template}} -> ${template} and strips ConfigParser
literal quotes. migrate_secrets: upper-cases keys and strips quotes.
"""

import importlib.util
import os
from configparser import ConfigParser

spec = importlib.util.spec_from_file_location(
    "es_config_migrate_yaml",
    os.path.join(os.path.dirname(__file__), "..", "es_config_migrate_yaml.py"),
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

migrate_es_config = mod.migrate_es_config
migrate_secrets = mod.migrate_secrets
strip_quotes = mod.strip_quotes


def make_cp(text):
    cp = ConfigParser(interpolation=None, inline_comment_prefixes="#")
    cp.read_string(text)
    return cp


class TestStripQuotes:
    def test_double(self):
        assert strip_quotes('"x"') == "x"

    def test_single(self):
        assert strip_quotes("'x'") == "x"

    def test_none(self):
        assert strip_quotes("x") == "x"


class TestMigrateEsConfig:
    def test_template_and_quote_rewrite(self):
        cp = make_cp(
            "[general]\n"
            "secrets={{secrets_dir}}/secrets.yml\n"
            "base=/etc/zm\n"
            "url={{host}}:{{port}}/api\n"
            "\n"
            "[fcm]\n"
            'token="quoted_value"\n'
        )
        result = migrate_es_config(cp)
        assert result == {
            "general": {
                # {{template}} -> ${template}
                "secrets": "${secrets_dir}/secrets.yml",
                "base": "/etc/zm",
                # multiple templates in one value both rewritten
                "url": "${host}:${port}/api",
            },
            "fcm": {
                # surrounding quotes stripped
                "token": "quoted_value",
            },
        }

    def test_empty_section_omitted(self):
        cp = make_cp("[empty]\n\n[has]\nk=v\n")
        result = migrate_es_config(cp)
        assert result == {"has": {"k": "v"}}


class TestMigrateSecrets:
    def test_keys_uppercased_values_unquoted(self):
        cp = make_cp(
            "[secrets]\n"
            "zm_user=admin\n"
            'zm_password="secret"\n'
        )
        result = migrate_secrets(cp)
        assert result == {
            "secrets": {
                "ZM_USER": "admin",
                "ZM_PASSWORD": "secret",
            }
        }

    def test_no_secrets_section_empty(self):
        cp = make_cp("[other]\nk=v\n")
        assert migrate_secrets(cp) == {}
