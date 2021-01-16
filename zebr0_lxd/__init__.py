import logging
from typing import Optional, List

import requests_unixsocket
import yaml
import zebr0

api_url = "http+unix://%2Fvar%2Fsnap%2Flxd%2Fcommon%2Flxd%2Funix.socket"


# parent (abstract) class for all lxd resources
class Resource:
    def __init__(self, session, collection_url, resource_config):
        self._session = session
        # relative url for this type of lxd resource (for example: "/1.0/containers")
        self._collection_url = collection_url
        # the configuration of this specific resource
        self._resource_config = resource_config
        # the logger for this resource
        self._logger = logging.getLogger("zebr0-lxd." + self.__class__.__name__)

    # creates this specific resource
    def create(self):
        self._log("checking")
        # only does something if the resource doesn't already exist (idempotence)
        if not self._exists():
            self._log("creating")
            self._session.post(self._get_full_collection_url(), json=self._resource_config)
            if not self._exists():
                raise Exception("resource creation failed")

    # deletes this specific resource
    def delete(self):
        self._log("checking")
        # only does something if the resource exists (idempotence)
        if self._exists():
            self._log("deleting")
            self._session.delete(self._get_full_element_url())
            if self._exists():
                raise Exception("resource deletion failed")

    # returns whether this specific resource exists or not...
    def _exists(self):
        # by checking if the resource's name is listed in the response of the collection url
        return any(filter(
            lambda a: a == self._get_element_url(),
            self._session.get(self._get_full_collection_url()).json().get("metadata")
        ))

    # returns the full url for this type of lxd resource
    def _get_full_collection_url(self):
        return api_url + self._collection_url

    # returns the resource's name
    def _get_name(self):
        return self._resource_config.get("name")

    # returns the resource's relative url
    def _get_element_url(self):
        return self._collection_url + "/" + self._get_name()

    # returns the resource's full url
    def _get_full_element_url(self):
        return self._get_full_collection_url() + "/" + self._get_name()

    # default logging function
    def _log(self, action):
        self._logger.info("%s %s", action, self._get_element_url())


class StoragePool(Resource):
    def __init__(self, session, resource_config):
        super().__init__(session, "/1.0/storage-pools", resource_config)


class Network(Resource):
    def __init__(self, session, resource_config):
        super().__init__(session, "/1.0/networks", resource_config)


class Profile(Resource):
    def __init__(self, session, resource_config):
        super().__init__(session, "/1.0/profiles", resource_config)


class Container(Resource):
    def __init__(self, session, resource_config):
        super().__init__(session, "/1.0/containers", resource_config)

    # starts the container
    def start(self):
        # only does something if the resource isn't already running (idempotence)
        if not self._is_running():
            self._log("starting")
            self._session.put(self._get_full_element_url() + "/state", json={"action": "start"})
            if not self._is_running():
                raise Exception("container starting failed")

    # stops the container
    def stop(self):
        # only does something if the resource is running (idempotence)
        if self._is_running():
            self._log("stopping")
            self._session.put(self._get_full_element_url() + "/state", json={"action": "stop"})
            if self._is_running():
                raise Exception("container stopping failed")

    # returns whether the container is running or not...
    def _is_running(self):
        # by checking its status in the metadata
        return self._exists() and self._session.get(self._get_full_element_url()).json().get("metadata").get("status") == "Running"


def create(session, config):
    for item in config.get("storage_pools") or []:
        StoragePool(session, item).create()
    for item in config.get("networks") or []:
        Network(session, item).create()
    for item in config.get("profiles") or []:
        Profile(session, item).create()
    for item in config.get("containers") or []:
        Container(session, item).create()


def start(session, config):
    for item in config.get("containers") or []:
        Container(session, item).start()


def stop(session, config):
    for item in config.get("containers") or []:
        Container(session, item).stop()


def delete(session, config):
    for item in config.get("containers") or []:
        Container(session, item).delete()
    for item in config.get("profiles") or []:
        Profile(session, item).delete()
    for item in config.get("networks") or []:
        Network(session, item).delete()
    for item in config.get("storage_pools") or []:
        StoragePool(session, item).delete()


def main(args: Optional[List[str]] = None) -> None:
    # this "hook" will be executed after each request to allow some generic treatment of the response
    # see http://docs.python-requests.org/en/master/user/advanced/#event-hooks
    def hook(response, **_):
        json = response.json()
        session_logger.debug(json)

        _type = json.get("type")

        if _type == "error":
            raise Exception(json)

        # this will wait for lxd asynchronous operations to be finished
        # see https://github.com/lxc/lxd/blob/master/doc/rest-api.md#background-operation
        if _type == "async":
            wait_json = session.get(api_url + json.get("operation") + "/wait").json()
            if wait_json.get("metadata").get("status_code") != 200:
                raise Exception(wait_json)

    # opens a unix socket session and adds the hook defined above
    session = requests_unixsocket.Session()
    session.hooks["response"].append(hook)
    session_logger = logging.getLogger("zebr0-lxd.Session")

    argparser = zebr0.build_argument_parser(description="zebr0 client to deploy an application to a local LXD environment")
    argparser.add_argument("command", choices=["create", "start", "stop", "delete"])
    args = argparser.parse_args(args)

    zebr0_service = zebr0.Client(args.url, args.levels, args.cache, args.configuration_file)

    # loads the configuration from zebr0 (uses the yaml baseloader to preserve all strings)
    config = yaml.load(zebr0_service.get("lxd-stack"), Loader=yaml.BaseLoader)

    # calls the method given as parameter
    globals()[args.command](session, config)

    session.close()