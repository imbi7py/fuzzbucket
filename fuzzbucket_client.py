#!/usr/bin/env python3
"""
A client for fuzzbucket.

Configuration is accepted via the following environment variables:

    FUZZBUCKET_URL - string URL of the fuzzbucket instance including path prefix
    FUZZBUCKET_CREDENTIALS - "github-user:fuzzbucket-token" string
    FUZZBUCKET_LOG_LEVEL - log level name (default="INFO")

"""
import argparse
import base64
import configparser
import contextlib
import fnmatch
import functools
import io
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
import textwrap
import typing
import urllib.parse
import urllib.request

try:
    import pkg_resources
except ImportError:  # pragma: no cover
    pkg_resources = None  # type: ignore


__version__ = "0.2.0"

LOG_LEVEL_DEBUG = "DEBUG"
DEFAULT_LOG_LEVEL = "INFO"


def default_client() -> "Client":
    return Client()


def config_logging(level_name: str = DEFAULT_LOG_LEVEL):
    logging.basicConfig(
        stream=sys.stdout,
        style="{",
        format="# {name}:{levelname}:{asctime}:: {message}",
        datefmt="%Y-%m-%dT%H%M%S",
        level=getattr(logging, level_name),
    )


def log_level() -> str:
    return os.environ.get("FUZZBUCKET_LOG_LEVEL", DEFAULT_LOG_LEVEL).strip().upper()


@functools.lru_cache(maxsize=2)
def full_version() -> str:
    try:
        source_dir = pathlib.Path(__file__).parent
        return (
            subprocess.check_output(
                [
                    "git",
                    f"--work-tree={source_dir}",
                    f"--git-dir={source_dir.joinpath('.git')}",
                    "describe",
                    "--always",
                    "--dirty",
                    "--tags",
                ],
                stderr=subprocess.DEVNULL,
            )
            .strip()
            .decode("utf-8")
            .replace("-", "+", 1)
            .replace("-", ".")
        )
    except subprocess.CalledProcessError:
        try:
            return pkg_resources.get_distribution("fuzzbucket-client").version
        except Exception:
            return __version__
    except Exception:
        if log_level() == LOG_LEVEL_DEBUG:
            log.exception("failed to get the extended version info")
        return __version__


log = logging.getLogger("fuzzbucket")


def main(sysargs: typing.List[str] = sys.argv[:]) -> int:
    client = default_client()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="store_true")
    parser.add_argument(
        "-D",
        "--debug",
        action="store_true",
        default=log_level() == LOG_LEVEL_DEBUG,
        help="enable debug logging",
    )
    subparsers = parser.add_subparsers(title="subcommands", help="additional help")

    parser_list = subparsers.add_parser("list", aliases=["ls"], help="List your boxes.")
    parser_list.set_defaults(func=client.list)

    parser_create = subparsers.add_parser(
        "create", aliases=["new"], help="Create a box."
    )
    parser_create.add_argument(
        "image", default=Client.default_image_alias, help="image alias or full AMI id"
    )
    parser_create.add_argument(
        "-n", "--name", help="custom name for box (generated if omitted)"
    )
    parser_create.add_argument(
        "-c",
        "--connect",
        action="store_true",
        help="add connect-specific security group for accessing ports 3939 and 13939",
    )
    parser_create.add_argument(
        "-T",
        "--ttl",
        default=str(3600 * 4),
        help="set the TTL for the box in seconds, after which it will be reaped",
    )
    parser_create.add_argument("-t", "--instance-type", default=None)
    parser_create.set_defaults(func=client.create)

    parser_delete = subparsers.add_parser(
        "delete", aliases=["rm"], help="Delete matching boxes."
    )
    parser_delete.add_argument("box_match")
    parser_delete.set_defaults(func=client.delete)

    parser_reboot = subparsers.add_parser(
        "reboot", aliases=["restart"], help="Reboot a box."
    )
    parser_reboot.add_argument("box")
    parser_reboot.set_defaults(func=client.reboot)

    parser_ssh = subparsers.add_parser("ssh", help="SSH into a box.")
    parser_ssh.add_argument(
        "-q", "--quiet", action="store_true", help="suppress box info header",
    )
    parser_ssh.usage = "usage: %(prog)s [-hq] box [ssh-arguments]"
    parser_ssh.description = textwrap.dedent(
        """
        SSH into a box, optionally passing arbitrary commands as positional
        arguments.  Additionally, stdio streams will be inherited by the ssh
        process in order to support piping.
        """
    )
    parser_ssh.epilog = textwrap.dedent(
        """
        NOTE: If no login is provided via the "-l" ssh option, a value will
        be guessed based on the box image alias.
        """
    )
    parser_ssh.add_argument("box")
    parser_ssh.set_defaults(func=client.ssh)

    parser_scp = subparsers.add_parser(
        "scp",
        help="SCP things into or out of a box.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser_scp.usage = "usage: %(prog)s [-h] box [scp-arguments]"
    parser_scp.description = textwrap.dedent(
        """
        SCP things into or out of a box, optionally passing arbitrary commands
        as positional arguments. Additionally, stdio streams will be inherited
        by the scp process in order to support piping.
        """
    )
    parser_scp.epilog = textwrap.dedent(
        """
        NOTE: If no login is provided in at least one of the source or
        destination arguments, a value will be guessed based on the box image
        alias.

        IMPORTANT: The fully-qualified address of the box will be substituted in
        the remaining command arguments wherever the literal "__BOX__" appears,
        e.g.:

        the command:
            %(prog)s boxname -r ./some/local/path __BOX__:/tmp/

        becomes:
            scp -r ./some/local/path username@boxname.fully.qualified.example.com:/tmp/

        the command:
            %(prog)s boxname -r 'altuser@__BOX__:/var/log/*.log' ./some/local/path/

        becomes:
            scp -r altuser@boxname.fully.qualified.example.com:/var/log/*.log \\
                   ./some/local/path/
        """
    )
    parser_scp.add_argument("box")
    parser_scp.set_defaults(func=client.scp)

    parser_list_aliases = subparsers.add_parser(
        "list-aliases", aliases=["la"], help="List known image aliases."
    )
    parser_list_aliases.set_defaults(func=client.list_aliases)

    parser_create_alias = subparsers.add_parser(
        "create-alias", help="Create an image alias."
    )
    parser_create_alias.add_argument("alias")
    parser_create_alias.add_argument("ami")
    parser_create_alias.set_defaults(func=client.create_alias)

    parser_delete_alias = subparsers.add_parser(
        "delete-alias", help="Delete an image alias."
    )
    parser_delete_alias.add_argument("alias")
    parser_delete_alias.set_defaults(func=client.delete_alias)

    known_args, unknown_args = parser.parse_known_args(sysargs[1:])
    config_logging(
        level_name=LOG_LEVEL_DEBUG if known_args.debug else DEFAULT_LOG_LEVEL
    )
    if known_args.version:
        print(f"fuzzbucket-client {full_version()}")
        return 0
    if not hasattr(known_args, "func"):
        log.debug(f"no subcommand func defined in namespace={known_args!r}")
        parser.print_help()
        return 2
    if known_args.func(known_args, unknown_args):
        return 0
    return 86


def _command(method):
    def handle_exc(exc):
        msg = f"command {method.__name__!r} failed"
        if log_level() == LOG_LEVEL_DEBUG:
            log.exception(msg)
        else:
            log.error(f"{msg} err={exc!r}")
        return False

    def wrapper(self, known_args, unknown_args):
        try:
            self._setup()
            return method(self, known_args, unknown_args)
        except urllib.request.HTTPError as exc:
            try:
                response = json.load(exc)
                log.error(
                    f"command {method.__name__!r} failed err={response.get('error')!r}"
                )
                return False
            except Exception as exc:
                return handle_exc(exc)
        except Exception as exc:
            return handle_exc(exc)

    return wrapper


class Client:
    default_instance_type = "t3.small"
    default_image_alias = "ubuntu18"
    default_instance_types = {
        "centos6": "t2.small",
        "rhel6": "t2.small",
        "sles12": "t2.small",
        None: default_instance_type,
    }
    default_ssh_user = "ec2-user"
    default_ssh_users = {
        "centos": "centos",
        "rhel": default_ssh_user,
        "sles": default_ssh_user,
        "suse": default_ssh_user,
        "ubuntu": "ubuntu",
    }

    def __init__(self, env=None):
        self._env = env if env is not None else dict(os.environ)
        self._cached_url_opener = None

    def _setup(self):
        if self._url is None:
            raise ValueError("missing url")
        if self._credentials is None:
            raise ValueError("missing credentials")

    @_command
    def list(self, *_):
        log.debug(f"fetching boxes for user={self._user!r}")
        boxes = self._list_boxes()
        log.info(f"fetched boxes for user={self._user!r} count={len(boxes)}")
        print(self._boxes_to_ini(boxes), end="")
        return True

    @_command
    def create(self, known_args, _):
        payload = {
            "instance_type": known_args.instance_type,
            "ttl": known_args.ttl,
        }
        if known_args.image.startswith("ami-"):
            payload["ami"] = known_args.image
        else:
            payload["image_alias"] = known_args.image

        if payload["instance_type"] is None:
            payload["instance_type"] = self.default_instance_types.get(
                payload.get("image_alias"), self.default_instance_type,
            )
        if known_args.connect:
            payload["connect"] = "1"
        if known_args.name != "":
            payload["name"] = known_args.name
        req = self._build_request(
            os.path.join(self._url, "box"),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        raw_response = {}
        try:
            with self._urlopen(req) as response:
                raw_response = json.load(response)
            log.info(f"created box for user={self._user!r}")
        except urllib.request.HTTPError as exc:
            if exc.code == 409:
                log.warning("matching box already exists")
                raw_response = json.load(exc)
            else:
                raise exc

        print(self._boxes_to_ini(raw_response["boxes"]), end="")
        return True

    @_command
    def delete(self, known_args, _):
        matching_boxes = self._find_boxes(known_args.box_match)
        if matching_boxes is None:
            log.error(f"no boxes found matching {known_args.box_match!r}")
            return False
        for matching_box in matching_boxes:
            req = self._build_request(
                os.path.join(self._url, "box", matching_box["instance_id"]),
                method="DELETE",
            )
            with self._urlopen(req) as response:
                _ = response.read()
            log.info(f"deleted box for user={self._user!r} name={matching_box['name']}")
            print(self._boxes_to_ini([matching_box]), end="")
        return True

    @_command
    def reboot(self, known_args, _):
        matching_box = self._find_box(known_args.box)
        if matching_box is None:
            log.error(f"no box found matching {known_args.box!r}")
            return False
        req = self._build_request(
            os.path.join(self._url, "reboot", matching_box["instance_id"]),
            method="POST",
        )
        with self._urlopen(req) as response:
            _ = response.read()
        log.info(f"rebooted box for user={self._user!r} box={matching_box['name']!r}")
        print(self._boxes_to_ini([matching_box]), end="")
        return True

    @_command
    def ssh(self, known_args, unknown_args):
        matching_box, ok = self._resolve_sshable_box(known_args.box)
        if not ok:
            return False
        ssh_command = self._build_ssh_command(matching_box, unknown_args)
        if not known_args.quiet:
            log.info(
                f"sshing into matching_box={matching_box['name']!r} "
                + f"ssh_command={ssh_command!r}"
            )
            print(self._boxes_to_ini([matching_box]), end="")
        sys.stdout.flush()
        sys.stderr.flush()
        os.execvp("ssh", ssh_command)
        return True

    @_command
    def scp(self, known_args, unknown_args):
        matching_box, ok = self._resolve_sshable_box(known_args.box)
        if not ok:
            return False
        scp_command = self._build_scp_command(matching_box, unknown_args)
        log.info(
            f"scping with matching_box={matching_box['name']!r} "
            + f"scp_command={scp_command!r}"
        )
        print(self._boxes_to_ini([matching_box]), end="")
        sys.stdout.flush()
        sys.stderr.flush()
        os.execvp("scp", scp_command)
        return True

    @_command
    def list_aliases(self, *_):
        req = self._build_request(os.path.join(self._url, "image-alias"))
        raw_response = {}
        with self._urlopen(req) as response:
            raw_response = json.load(response)
        if "image_aliases" not in raw_response:
            log.error("failed to fetch image aliases")
            return False
        print(self._image_aliases_to_ini(raw_response["image_aliases"]), end="")
        return True

    @_command
    def create_alias(self, known_args, _):
        payload = {"alias": known_args.alias, "ami": known_args.ami}
        req = self._build_request(
            os.path.join(self._url, "image-alias"),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        raw_response = {}
        with self._urlopen(req) as response:
            raw_response = json.load(response)
        log.debug(f"raw created alias response={raw_response!r}")
        created = raw_response["image_aliases"][0]
        for key, value in created.items():
            log.info(
                f"created alias for user={self._user!r} alias={key} " + f"ami={value}"
            )
        print(self._image_aliases_to_ini(created), end="")
        return True

    @_command
    def delete_alias(self, known_args, _):
        req = self._build_request(
            os.path.join(self._url, "image-alias", known_args.alias), method="DELETE"
        )
        with self._urlopen(req) as response:
            _ = response.read()
        log.info(f"deleted alias for user={self._user!r} alias={known_args.alias}")
        return True

    def _find_box(self, box_search):
        results = self._find_boxes(box_search)
        if results is None:
            return None
        return results[0]

    def _find_boxes(self, box_search):
        boxes = self._list_boxes()
        results = []
        for box in boxes:
            log.debug(f"finding box_search={box_search!r} considering box={box!r}")
            if box.get("name") is not None and fnmatch.fnmatchcase(
                box["name"], box_search
            ):
                results.append(box)
                continue
            if box.get("image_alias") is not None and fnmatch.fnmatchcase(
                box["image_alias"], box_search
            ):
                results.append(box)
                continue
        if len(results) == 0:
            return None
        return results

    def _list_boxes(self):
        req = self._build_request(self._url)
        raw_response = {}
        with self._urlopen(req) as response:
            raw_response = json.load(response)
        return raw_response["boxes"]

    @contextlib.contextmanager
    def _urlopen(self, request):
        log.debug(
            f"attempting request user={self._user!r} method={request.method!r} "
            + f"url={request.full_url!r}"
        )
        with urllib.request.urlopen(request) as response:
            yield response

    @property
    def _url(self):
        return self._env.get("FUZZBUCKET_URL")

    @property
    def _credentials(self):
        return self._env.get("FUZZBUCKET_CREDENTIALS")

    @property
    def _user(self):
        return self._credentials.split(":")[0].split("--")[0]

    def _build_request(self, url, data=None, headers=None, method="GET"):
        headers = headers if headers is not None else {}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        if not req.has_header("Authorization"):
            creds_b64 = base64.b64encode(self._credentials.encode("utf-8"))
            creds_b64 = creds_b64.decode("utf-8")
            req.headers["Authorization"] = f"basic {creds_b64}"
        return req

    def _resolve_sshable_box(self, box):
        matching_box = self._find_box(box)
        if matching_box is None:
            log.error(f"no box found matching {box!r}")
            return None, False
        if matching_box.get("public_dns_name") is None:
            log.error(f"no public dns name found for box={matching_box['name']}")
            return None, False
        return matching_box, True

    def _build_ssh_command(self, box, unknown_args):
        if "-l" not in unknown_args:
            unknown_args = [
                "-l",
                self._guess_ssh_user(
                    box.get("image_alias", self.default_image_alias),
                    self.default_ssh_user,
                ),
            ] + unknown_args
        return ["ssh", box.get("public_dns_name")] + self._with_ssh_opts(unknown_args)

    def _build_scp_command(self, box, unknown_args):
        for i, value in enumerate(unknown_args):
            if "__BOX__" not in value:
                continue

            box_value = box["public_dns_name"]
            if "@" not in value:
                box_value = "@".join(
                    [
                        self._guess_ssh_user(
                            box.get("image_alias", self.default_image_alias),
                            self.default_ssh_user,
                        ),
                        box_value,
                    ]
                )

            unknown_args[i] = value.replace("__BOX__", box_value)
        return ["scp"] + self._with_ssh_opts(unknown_args)

    def _with_ssh_opts(self, unknown_args: typing.List[str]) -> typing.List[str]:
        unknown_args_string = " ".join(unknown_args)
        if (
            re.search(
                " -o StrictHostKeyChecking=.+", unknown_args_string, re.IGNORECASE
            )
            is None
        ):
            unknown_args = ["-o", "StrictHostKeyChecking=no"] + unknown_args
        if (
            re.search(" -o UserKnownHostsFile=.+", unknown_args_string, re.IGNORECASE)
            is None
        ):
            unknown_args = ["-o", "UserKnownHostsFile=/dev/null"] + unknown_args
        return unknown_args

    @staticmethod
    def _boxes_to_ini(boxes):
        boxes_ini = configparser.ConfigParser()
        for box in boxes:
            boxes_ini.add_section(box["name"])
            if box.get("public_ip") is None:
                box["public_ip"] = "(pending)"
            for key, value in box.items():
                if value is None:
                    continue
                boxes_ini.set(box["name"], str(key), str(value))
        buf = io.StringIO()
        boxes_ini.write(buf)
        buf.seek(0)
        return buf.read()

    @staticmethod
    def _image_aliases_to_ini(image_aliases):
        image_aliases_ini = configparser.ConfigParser()
        image_aliases_ini.add_section("image_aliases")
        for alias, ami in sorted(image_aliases.items()):
            image_aliases_ini.set("image_aliases", alias, ami)
        buf = io.StringIO()
        image_aliases_ini.write(buf)
        buf.seek(0)
        return buf.read()

    @classmethod
    def _guess_ssh_user(cls, image_alias, default=default_ssh_user):
        image_alias = image_alias.lower()
        for prefix, user in cls.default_ssh_users.items():
            if image_alias.startswith(prefix):
                return user
        return default


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
