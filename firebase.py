"""

    Firebase wrapper for Netskrafl

    Copyright (C) 2020 Miðeind ehf.
    Original author: Vilhjálmur Þorsteinsson

    The GNU General Public License, version 3, applies to this software.
    For further information, see https://github.com/mideind/Netskrafl

    This module implements a thin wrapper around the Google Firebase
    functionality used to send push notifications to clients.

"""

import os
import time
import base64
import json
import threading
import logging
from datetime import datetime

import httplib2

from oauth2client.client import GoogleCredentials

from firebase_admin import initialize_app, auth


_PROJECT_ID = os.environ.get("PROJECT_ID", "")
_FIREBASE_DB_URL = "https://{0}.firebaseio.com".format(_PROJECT_ID)
_IDENTITY_ENDPOINT = (
    "https://identitytoolkit.googleapis.com/"
    "google.identity.identitytoolkit.v1.IdentityToolkit"
)
_FIREBASE_SCOPES = [
    "https://www.googleapis.com/auth/firebase.database",
    "https://www.googleapis.com/auth/userinfo.email",
]
_TIMEOUT = 15  # Seconds

_HEADERS = {"Connection": "keep-alive"}

# Initialize thread-local storage
_tls = threading.local()

assert _PROJECT_ID, "PROJECT_ID environment variable not defined"


def _get_http():
    """ Provides an authorized HTTP object, one per thread """
    if not hasattr(_tls, "_HTTP") or _tls._HTTP is None:
        http = httplib2.Http(timeout=_TIMEOUT)
        # Use application default credentials to make the Firebase calls
        # https://firebase.google.com/docs/reference/rest/database/user-auth
        creds = GoogleCredentials.get_application_default().create_scoped(
            _FIREBASE_SCOPES
        )
        creds.authorize(http)
        creds.refresh(http)
        _tls._HTTP = http
    return _tls._HTTP


def _request(*args, **kwargs):
    """ Attempt to post a Firebase request, with recovery on a BrokenPipeError """
    try:
        return _get_http().request(*args, headers=_HEADERS, **kwargs)
    except BrokenPipeError:
        # Attempt recovery by creating a new httplib2.Http object and
        # forcing re-generation of the credentials
        _tls._HTTP = None
    return _get_http().request(*args, headers=_HEADERS, **kwargs)


def _firebase_put(path, message=None):
    """ Writes data to Firebase.

    An HTTP PUT writes an entire object at the given database path. Updates to
    fields cannot be performed without overwriting the entire object

    Args:
        path - the url to the Firebase object to write.
        value - a json string.
    """
    return _request(path, method="PUT", body=message)


def _firebase_get(path):
    """ Read the data at the given path.

    An HTTP GET request allows reading of data at a particular path.
    A successful request will be indicated by a 200 OK HTTP status code.
    The response will contain the data being retrieved.

    Args:
        path - the url to the Firebase object to read.
    """
    return _request(path, method="GET")


def _firebase_patch(path, message):
    """ Update the data at the given path.

    An HTTP GET request allows reading of data at a particular path.
    A successful request will be indicated by a 200 OK HTTP status code.
    The response will contain the data being retrieved.

    Args:
        path - the url to the Firebase object to read.
    """
    return _request(path, method="PATCH", body=message)


def _firebase_delete(path):
    """ Delete the data at the given path.

    An HTTP DELETE request allows deleting of the data at the given path.
    A successful request will be indicated by a 200 OK HTTP status code.

    Args:
        path - the url to the Firebase object to delete.
    """
    return _request(path, method="DELETE")


def send_message(message, *args):
    """ Updates data in Firebase. If a message object is provided, then it updates
        the data at the given location (whose path is built as a concatenation
        of the *args list) with the message using the PATCH http method.
        If no message is provided, the data at this location is deleted
        using the DELETE http method.
    """
    try:
        if args:
            url = "/".join((_FIREBASE_DB_URL,) + args) + ".json"
        else:
            url = _FIREBASE_DB_URL + "/.json"
        if message is None:
            response, _ = _firebase_delete(path=url)
        else:
            response, _ = _firebase_patch(
                path=url + "?print=silent", message=json.dumps(message)
            )
        # If all is well and good, "200" (OK) or "204" (No Content) is returned in the status field
        return response["status"] in ("200", "204")
    except httplib2.HttpLib2Error as e:
        logging.warning("Exception [{}] in firebase.send_message()".format(repr(e)))
        return False


def send_update(*args):
    """ Updates the path endpoint to contain the current UTC timestamp """
    assert args, "Firebase path cannot be empty"
    endpoint = args[-1]
    value = {endpoint: datetime.utcnow().isoformat()}
    return send_message(value, *args[:-1])


def check_wait(user_id, opp_id):
    """ Return True if the user user_id is waiting for the opponent opponent_id """
    try:
        url = "{}/user/{}/wait/{}.json".format(_FIREBASE_DB_URL, user_id, opp_id)
        response, body = _firebase_get(path=url)
        if response["status"] != "200":
            return False
        msg = json.loads(body) if body else None
        return msg is True  # Return False if msg is dict, None or False
    except httplib2.HttpLib2Error as e:
        logging.warning(
            "Exception [{}] raised in firebase.check_wait()".format(repr(e))
        )
        return False


def check_presence(user_id):
    """ Check whether the given user has at least one active connection """
    try:
        url = "{}/connection/{}.json".format(_FIREBASE_DB_URL, user_id)
        response, body = _firebase_get(path=url)
        if response["status"] != "200":
            return False
        msg = json.loads(body) if body else None
        return bool(msg)
    except httplib2.HttpLib2Error as e:
        logging.warning(
            "Exception [{}] raised in firebase.check_presence()".format(repr(e))
        )
        return False


_USERLIST_LOCK = threading.Lock()


def get_connected_users():
    """ Return a set of all presently connected users """
    with _USERLIST_LOCK:
        # Serialize access to the connected user list
        url = "{}/connection.json?shallow=true".format(_FIREBASE_DB_URL)
        try:
            response, body = _firebase_get(path=url)
        except httplib2.HttpLib2Error as e:
            logging.warning(
                "Exception [{}] raised in firebase.get_connected_users()".format(
                    repr(e)
                )
            )
            return set()
        if response["status"] != "200":
            return set()
        msg = json.loads(body) if body else None
        if not msg:
            return set()
        return set(msg.keys())


_firebase_app = None


def create_custom_token(uid, valid_minutes=60):
    """ Create a secure token for the given id.

        This method is used to create secure custom JWT tokens to be passed to
        clients. It takes a unique id that will be used by Firebase's
        security rules to prevent unauthorized access. In this case, the uid will
        be the channel id which is a combination of a user id and a game id.
    """
    global _firebase_app
    if _firebase_app is None:
        _firebase_app = initialize_app()
    return auth.create_custom_token(uid).decode()
