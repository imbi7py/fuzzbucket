import base64
import json
import random
import time

import boto3
import pytest

from moto import mock_ec2

import fuzzbucket


@pytest.fixture
def authd_event():
    return {
        "headers": {"Authorization": base64.b64encode(b"pytest:zzz").decode("utf-8")}
    }


@pytest.fixture
def env():
    return {
        "CF_VPC": "vpc-fafafafafaf",
    }


@pytest.fixture
def pubkey():
    return "".join(
        [
            "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCcKKyTEzdI6zFMEmhbXSLemjTskw620yumv",
            "bhoGwrY4zun/1cz+obxk1DZ+j0AfVTA9EQCr7AsFX3KRrevEBgHvWcK3vDp2h2pz/naM40SwF",
            "dLK1+2G8vFy6zWZlFvQSNj8D6pxKGb6e0I3oVRBPd1V8z0AIswe2/9BiDi1K3Mx4yDoidZwnU",
            "qweCCWwv3Y6nHkveEtVZlm8btGrlo2ya4IdCV2/KUK7FDbhGkLS7ZidVi+hS2GcrOTZYAkQW5",
            "aS6r/QYTQGz94RjmyOFam5GhW5zboFdYnF9QD4WUGr4Gn9iI6QxaV50UXv37v+6pCaNYMPUjI",
            f"SFQFMNhHnnMwcnx pytest@nowhere{random.randint(100, 999)}",
        ]
    )


@mock_ec2
def test_list_boxes(authd_event, env):
    client = boto3.client("ec2")
    response = fuzzbucket.list_boxes(authd_event, None, client=client, env=env)
    assert response["statusCode"] == 200
    assert response["body"] is not None
    body = json.loads(response["body"])
    assert "boxes" in body
    assert body["boxes"] is not None


@mock_ec2
def test_list_boxes_forbidden(env):
    client = boto3.client("ec2")
    response = fuzzbucket.list_boxes({}, None, client=client, env=env)
    assert response["statusCode"] == 403


@mock_ec2
def test_create_box(authd_event, env, monkeypatch, pubkey):
    client = boto3.client("ec2")
    with monkeypatch.context() as mp:
        mp.setattr(fuzzbucket, "_fetch_first_github_key", lambda u: pubkey)
        response = fuzzbucket.create_box(authd_event, None, client=client, env=env)
    assert response["statusCode"] == 200
    assert response["body"] is not None
    body = json.loads(response["body"])
    assert "boxes" in body
    assert body["boxes"] != []


@mock_ec2
def test_create_box_forbidden(env):
    client = boto3.client("ec2")
    response = fuzzbucket.create_box({}, None, client=client, env=env)
    assert response["statusCode"] == 403


@mock_ec2
def test_delete_box(authd_event, env, monkeypatch, pubkey):
    client = boto3.client("ec2")
    with monkeypatch.context() as mp:
        mp.setattr(fuzzbucket, "_fetch_first_github_key", lambda u: pubkey)
        response = fuzzbucket.create_box(authd_event, None, client=client, env=env)
    assert response is not None
    assert "body" in response
    body = json.loads(response["body"])
    assert "boxes" in body
    event = {"pathParameters": {"id": body["boxes"][0]["instance_id"]}}
    event.update(**authd_event)

    all_instances = client.describe_instances()
    with monkeypatch.context() as mp:

        def fake_describe_instances(*_args, **_kwargs):
            return all_instances

        mp.setattr(client, "describe_instances", fake_describe_instances)
        response = fuzzbucket.delete_box(event, None, client=client, env=env)
        assert response["statusCode"] == 204


@mock_ec2
def test_delete_box_forbidden(env):
    client = boto3.client("ec2")
    event = {"pathParameters": {"id": "i-fafafafafafaf"}}
    response = fuzzbucket.delete_box(event, None, client=client, env=env)
    assert response["statusCode"] == 403


@mock_ec2
def test_reboot_box(authd_event, env, monkeypatch, pubkey):
    client = boto3.client("ec2")
    with monkeypatch.context() as mp:
        mp.setattr(fuzzbucket, "_fetch_first_github_key", lambda u: pubkey)
        response = fuzzbucket.create_box(authd_event, None, client=client, env=env)
    assert response is not None
    assert "body" in response
    body = json.loads(response["body"])
    assert "boxes" in body
    event = {"pathParameters": {"id": body["boxes"][0]["instance_id"]}}
    event.update(**authd_event)

    all_instances = client.describe_instances()
    with monkeypatch.context() as mp:

        def fake_describe_instances(*_args, **_kwargs):
            return all_instances

        mp.setattr(client, "describe_instances", fake_describe_instances)
        response = fuzzbucket.reboot_box(event, None, client=client, env=env)
        assert response["statusCode"] == 204


@mock_ec2
def test_reboot_box_forbidden(env):
    client = boto3.client("ec2")
    event = {"pathParameters": {"id": "i-fafafafafafaf"}}
    response = fuzzbucket.reboot_box(event, None, client=client, env=env)
    assert response["statusCode"] == 403


def test_box():
    box = fuzzbucket.Box()
    box.instance_id = "i-fafafafafafafaf"
    assert box.age == "?"

    box.created_at = str(time.time() - 1000)
    for unit in ("d", "h", "m", "s"):
        assert box.age.count(unit) == 1

    assert "instance_id" in box.as_json()
    assert "age" in box.as_json()
