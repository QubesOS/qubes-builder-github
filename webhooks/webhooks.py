#!flask/bin/python3
# -*- encoding: utf8 -*-
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2020 Frédéric Pierret <frederic.pierret@qubes-os.org>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import os
import json
import logging
import importlib
import hmac
import hashlib

from flask import Flask, jsonify, request, Response

app = Flask(__name__)
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class ApiError(Exception):
    status_code = 400

    def __init__(self, message, status_code=None, payload=None):
        super().__init__()
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.payload = payload

    def to_dict(self):
        rv = dict(self.payload or ())
        rv["message"] = self.message
        return rv


def read_config():
    """
    {
      "services": [
        "trigger_build",
        "process_comment"
      ]
    }
    """
    config_path = os.environ.get("WEBHOOKS_CONFIG", "/home/user/webhooks/webhooks.conf")
    with open(config_path, "r") as cfd:
        conf = json.loads(cfd.read())

    if not conf.get("services"):
        raise AttributeError("Services not provided")

    return conf


# read local config
webhooks_config = read_config()


# begin flask app
@app.errorhandler(ApiError)
def handle_invalid_usage(error):
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response


@app.route("/api/services/<string:service_name>", methods=["POST"])
def run(service_name):
    """
    POST run service
    """
    event_type_github = request.headers.get("X-GitHub-Event", "")
    event_type_gitlab = request.headers.get("X-Gitlab-Event", "")
    if event_type_github not in (
        "push",
        "issue_comment",
        "pull_request",
    ) and event_type_gitlab not in ("Pipeline Hook", "Job Hook"):
        return Response("OK", status=200, mimetype="text/plain")

    payload_data = request.data
    if 'webhook_secret' in webhooks_config:
        if event_type_github:
            hmac_value = 'sha256=' + hmac.new(webhooks_config['webhook_secret'].encode(),
                                              payload_data,
                                              hashlib.sha256).hexdigest()
            if hmac_value != request.headers.get('X-Hub-Signature-256'):
                return Response('invalid hmac', status=403, mimetype='text/plain')
        elif event_type_gitlab:
            if webhooks_config['webhook_secret'] != request.headers.get('X-Gitlab-Token'):
                return Response('invalid token', status=403, mimetype='text/plain')
        else:
            return Reponse('no even type', status=400, mimetype='text/plain')

    if service_name not in webhooks_config.get("services", []):
        raise ApiError("Unknown service", status_code=404)

    try:
        module = importlib.import_module("services.%s" % service_name)
    except (ImportError, ModuleNotFoundError, TypeError):
        raise ApiError("Cannot import service", status_code=500)

    service = module.Service()
    payload = json.loads(payload_data)
    service.handle(payload)

    # return Response("OK", status=200, mimetype='application/json')
    return Response("OK", status=200, mimetype="text/plain")


if __name__ == "__main__":
    app.run(debug=True)
