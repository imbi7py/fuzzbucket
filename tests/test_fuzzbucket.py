import base64
import os
import random
import time

import boto3
import pytest

from moto import mock_ec2, mock_dynamodb2

import fuzzbucket
import fuzzbucket.app
import fuzzbucket.reaper

from fuzzbucket.app import app
from fuzzbucket.box import Box


@pytest.fixture(autouse=True)
def env_setup():
    os.environ.setdefault("CF_VPC", "vpc-fafafafaf")
    os.environ.setdefault("FUZZBUCKET_IMAGE_ALIASES_TABLE_NAME", "image-aliases")
    app.testing = True


@pytest.fixture
def authd_headers():
    return [("Authorization", base64.b64encode(b"pytest:zzz").decode("utf-8"))]


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


def setup_dynamodb_tables(dynamodb_client):
    dynamodb_client.create_table(
        AttributeDefinitions=[
            dict(AttributeName="Alias", AttributeType="S"),
            dict(AttributeName="AMI", AttributeType="S"),
            dict(AttributeName="User", AttributeType="S"),
        ],
        KeySchema=[
            dict(AttributeName="Alias", KeyType="HASH"),
            dict(AttributeName="AMI", KeyType="HASH"),
            dict(AttributeName="User", KeyType="HASH"),
        ],
        TableName=os.getenv("FUZZBUCKET_IMAGE_ALIASES_TABLE_NAME"),
    )
    for alias, ami in {
        "ubuntu18": "ami-fafafafafaf",
        "rhel8": "ami-fafafafafaa",
    }.items():
        dynamodb_client.put_item(
            TableName=os.getenv("FUZZBUCKET_IMAGE_ALIASES_TABLE_NAME"),
            Item=dict(User=dict(S="testing"), Alias=dict(S=alias), AMI=dict(S=ami),),
        )


@mock_ec2
def test_list_boxes(authd_headers, monkeypatch):
    monkeypatch.setattr(fuzzbucket.app, "get_ec2_client", lambda: boto3.client("ec2"))
    response = None
    with app.test_client() as c:
        response = c.get("/", headers=authd_headers)
    assert response is not None
    assert response.status_code == 200
    assert response.json is not None
    assert "boxes" in response.json
    assert response.json["boxes"] is not None


@mock_ec2
def test_list_boxes_forbidden(monkeypatch):
    monkeypatch.setattr(fuzzbucket.app, "get_ec2_client", lambda: boto3.client("ec2"))
    response = None
    with app.test_client() as c:
        response = c.get("/")
    assert response.status_code == 403


@mock_ec2
@mock_dynamodb2
def test_create_box(authd_headers, monkeypatch, pubkey):
    monkeypatch.setattr(fuzzbucket.app, "get_ec2_client", lambda: boto3.client("ec2"))
    monkeypatch.setattr(
        fuzzbucket.app, "get_dynamodb_client", lambda: boto3.client("dynamodb")
    )
    response = None
    with monkeypatch.context() as mp:
        mp.setattr(fuzzbucket.app, "_fetch_first_github_key", lambda u: pubkey)
        with app.test_client() as c:
            response = c.post(
                "/box", json={"ami": "ami-fafafafafaf"}, headers=authd_headers,
            )
    assert response is not None
    assert response.status_code == 201
    assert response.json is not None
    assert "boxes" in response.json
    assert response.json["boxes"] != []


@mock_ec2
@mock_dynamodb2
def test_create_box_forbidden(monkeypatch):
    monkeypatch.setattr(fuzzbucket.app, "get_ec2_client", lambda: boto3.client("ec2"))
    monkeypatch.setattr(
        fuzzbucket.app, "get_dynamodb_client", lambda: boto3.client("dynamodb")
    )
    response = None
    with app.test_client() as c:
        response = c.post("/box", json={"ami": "ami-fafafafafafaf"})
    assert response is not None
    assert response.status_code == 403


@mock_ec2
@mock_dynamodb2
def test_delete_box(authd_headers, monkeypatch, pubkey):
    with app.app_context():
        ec2_client = boto3.client("ec2")
        monkeypatch.setattr(fuzzbucket.app, "get_ec2_client", lambda: ec2_client)
        monkeypatch.setattr(
            fuzzbucket.app, "get_dynamodb_client", lambda: boto3.client("dynamodb")
        )
        response = None
        with monkeypatch.context() as mp:
            mp.setattr(fuzzbucket.app, "_fetch_first_github_key", lambda u: pubkey)
            with app.test_client() as c:
                response = c.post(
                    "/box", json={"ami": "ami-fafafafafaf"}, headers=authd_headers
                )
        assert response is not None
        assert "boxes" in response.json

        with app.test_client() as c:
            all_instances = ec2_client.describe_instances()
            with monkeypatch.context() as mp:

                def fake_describe_instances(*_args, **_kwargs):
                    return all_instances

                mp.setattr(
                    ec2_client, "describe_instances", fake_describe_instances,
                )
                response = c.delete(
                    f'/box/{response.json["boxes"][0]["instance_id"]}',
                    headers=authd_headers,
                )
                assert response.status_code == 204


@mock_ec2
def test_delete_box_forbidden(monkeypatch):
    monkeypatch.setattr(fuzzbucket.app, "get_ec2_client", lambda: boto3.client("ec2"))
    response = None
    with app.test_client() as c:
        response = c.delete("/box/i-fafafafaf")
    assert response is not None
    assert response.status_code == 403


@mock_ec2
@mock_dynamodb2
def test_reboot_box(authd_headers, monkeypatch, pubkey):
    with app.app_context():
        ec2_client = boto3.client("ec2")
        monkeypatch.setattr(fuzzbucket.app, "get_ec2_client", lambda: ec2_client)
        monkeypatch.setattr(
            fuzzbucket.app, "get_dynamodb_client", lambda: boto3.client("dynamodb")
        )
        response = None
        with monkeypatch.context() as mp:
            mp.setattr(fuzzbucket.app, "_fetch_first_github_key", lambda u: pubkey)
            with app.test_client() as c:
                response = c.post(
                    "/box", json={"ami": "ami-fafafafafaf"}, headers=authd_headers
                )
        assert response is not None
        assert "boxes" in response.json

        with app.test_client() as c:
            all_instances = ec2_client.describe_instances()
            with monkeypatch.context() as mp:

                def fake_describe_instances(*_args, **_kwargs):
                    return all_instances

                mp.setattr(
                    ec2_client, "describe_instances", fake_describe_instances,
                )
                response = c.post(
                    f'/reboot/{response.json["boxes"][0]["instance_id"]}',
                    headers=authd_headers,
                )
                assert response.status_code == 204


@mock_ec2
def test_reboot_box_forbidden(monkeypatch):
    monkeypatch.setattr(fuzzbucket.app, "get_ec2_client", lambda: boto3.client("ec2"))
    response = None
    with app.test_client() as c:
        response = c.post("/reboot/i-fafafafaf")
    assert response is not None
    assert response.status_code == 403


@mock_ec2
@mock_dynamodb2
def test_list_image_aliases(authd_headers, monkeypatch):
    dynamodb_client = boto3.client("dynamodb")
    monkeypatch.setattr(fuzzbucket.app, "get_dynamodb_client", lambda: dynamodb_client)
    setup_dynamodb_tables(dynamodb_client)
    response = None
    with app.test_client() as c:
        response = c.get("/image-alias", headers=authd_headers)
    assert response is not None
    assert response.status_code == 200


@mock_ec2
@mock_dynamodb2
def test_list_image_aliases_forbidden(monkeypatch):
    monkeypatch.setattr(
        fuzzbucket.app, "get_dynamodb_client", lambda: boto3.client("dynamodb")
    )
    response = None
    with app.test_client() as c:
        response = c.get("/image-alias")
    assert response is not None
    assert response.status_code == 403


@mock_ec2
@mock_dynamodb2
def test_create_image_alias(authd_headers, monkeypatch):
    dynamodb_client = boto3.client("dynamodb")
    monkeypatch.setattr(fuzzbucket.app, "get_dynamodb_client", lambda: dynamodb_client)
    setup_dynamodb_tables(dynamodb_client)
    response = None
    with app.test_client() as c:
        response = c.post(
            "/image-alias",
            json={"alias": "yikes", "ami": "ami-fafababacaca"},
            headers=authd_headers,
        )
    assert response is not None
    assert response.status_code == 201


@mock_ec2
@mock_dynamodb2
def test_reap_boxes(authd_headers, monkeypatch, pubkey):
    monkeypatch.setattr(fuzzbucket.app, "get_ec2_client", lambda: boto3.client("ec2"))
    monkeypatch.setattr(
        fuzzbucket.reaper, "get_ec2_client", lambda: boto3.client("ec2")
    )
    monkeypatch.setattr(
        fuzzbucket.app, "get_dynamodb_client", lambda: boto3.client("dynamodb")
    )
    response = None
    with monkeypatch.context() as mp:
        mp.setattr(fuzzbucket.app, "_fetch_first_github_key", lambda u: pubkey)
        with app.test_client() as c:
            response = c.post(
                "/box",
                json={"ttl": "-1", "ami": "ami-fafafafafaf"},
                headers=authd_headers,
            )
    assert response is not None
    assert "boxes" in response.json
    instance_id = response.json["boxes"][0]["instance_id"]
    assert instance_id != ""

    the_future = time.time() + 3600
    with monkeypatch.context() as mp:
        ec2_client = boto3.client("ec2")

        def fake_list_vpc_boxes(ec2_client, vpc_id):
            return [Box.from_dict(box) for box in response.json["boxes"]]

        mp.setattr(time, "time", lambda: the_future)
        mp.setattr(fuzzbucket.reaper, "list_vpc_boxes", fake_list_vpc_boxes)
        response = fuzzbucket.reaper.reap_boxes(
            None, None, ec2_client=ec2_client, env={"CF_VPC": "vpc-fafafafafaf"}
        )
        assert response != {}
    assert instance_id not in [
        box.instance_id
        for box in fuzzbucket.list_boxes_filtered(
            ec2_client, fuzzbucket.DEFAULT_FILTERS
        )
    ]


def test_box():
    box = Box()
    box.instance_id = "i-fafafafafafafaf"
    assert box.age == "?"

    box.created_at = str(time.time() - 1000)
    for unit in ("d", "h", "m", "s"):
        assert box.age.count(unit) == 1

    assert "instance_id" in box.as_json()
    assert "age" in box.as_json()
