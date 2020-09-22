:tocdepth: 1

.. sectnum::

Abstract
========

Authentication tokens will be used by the science platform as web authentication credentials, for API and service calls from outside the Science Platform, and for internal service-to-service and notebook-to-service calls.
This document lays out the technical design of the token management component, satisfying the requirements given in `SQR-044`_.

.. _SQR-044: https://sqr-044.lsst.io/

Storage
=======

Storage for authentication tokens is divided into two backend stores: a SQL database and Redis.
Redis is used for the token itself, including the authentication secret.
It contains enough information to verify the authentication of a request and return the user's identity.
The SQL database stores metadata about a user's tokens, including the list of currently valid tokens, their relationships to each other, and a history of where they have been used from.

Use of two separate storage systems is unfortunate extra complexity, but Redis is poorly suited to store relational data about tokens or long-term history, while PostgreSQL is poorly suited for quickly handling a high volume of checks for token validity.

Token format
------------

A token is of the form ``gsh-<key>.<secret>``.
The ``gsh-`` part is a fixed prefix to make it easy to identify tokens.
The ``<key>`` is the Redis key under which data about the token is stored.
The ``<secret>`` is an opaque value used to prove that the holder of the token is allowed to use it.
Wherever the token is named, such as in UIs, only the ``<key>`` component is given, omitting the secret.
When the token is presented for authentication, the secret provided is checked against the stored secret for that key.
Checking the secret prevents someone who can list the keys in the Redis session store from using those keys as session handles.

Database
--------

All schemas shown below are for PostgreSQL.

Each user has one or more authentication tokens.
Those tokens may be of the following types:

- **session**: A web session
- **user**: User-generated token for API access
- **notebook**: Automatically generated token for notebook use
- **internal**: Internal token used for service-to-service authentication

An index of current extant tokens is stored via the following schema:

.. code-block:: sql

   CREATE TYPE token_enum AS ENUM ('session', 'user', 'notebook', 'internal');
   CREATE TABLE tokens (
       PRIMARY KEY (key),
       key        VARCHAR(64)  NOT NULL,
       name       VARCHAR(64),
       username   VARCHAR(64)  NOT NULL,
       token_type token_enum   NOT NULL,
       scope      VARCHAR(256) NOT NULL,
       actor      VARCHAR(64),
       created    TIMESTAMP    NOT NULL,
       last_used  TIMESTAMP,
       expires    TIMESTAMP,
       UNIQUE(username, name)
   );
   CREATE INDEX tokens_by_username ON tokens (username, name);

The ``actor`` column is only used by internal tokens.
It stores an identifier for the service to which the token was issued and which is acting on behalf of a user.

Internal tokens are derived from non-internal tokens.
That relationship is captured by the following schema:

.. code-block:: sql

   CREATE TABLE subtokens (
       PRIMARY KEY (id),
       id     SERIAL      NOT NULL,
       parent VARCHAR(64)          REFERENCES tokens ON DELETE SET NULL,
       child  VARCHAR(64) NOT NULL REFERENCES tokens ON DELETE CASCADE
   );
   CREATE INDEX subtokens_by_scope ON subtokens (parent, scope);

Finally, token usage information is stored in a history table.
This will not hold every usage, since that data could be overwhelming for web sessions and other instances of high-frequency calls.
However, it will attempt to capture the most recent uses from a given IP address.

It doubles as the web session history table, since web sessions are another type of token.

.. code-block:: sql

   CREATE TABLE token_history (
       PRIMARY KEY (id),
       id         SERIAL       NOT NULL,
       key        VARCHAR(64)  NOT NULL,
       name       VARCHAR(64)  NOT NULL,
       username   VARCHAR(64)  NOT NULL,
       token_type token_enum   NOT NULL,
       parent     VARCHAR(64),
       scope      VARCHAR(256) NOT NULL,
       actor      VARCHAR(64),
       ip_address VARCHAR(64),
       when       TIMESTAMP    NOT NULL
   );
   CREATE INDEX token_history_by_username (username, when);

This table stores data even for tokens that have been deleted, so it duplicates some information from the ``tokens`` table rather than adding a foreign key.

Redis
-----

Redis stores a key for each token.
The Redis key is ``token:<key>`` where ``<key>`` is the key portion of the token, corresponding to the primary key of the ``tokens`` table.
The value is an encrypted JSON document with the following keys:

- **secret**: The corresponding secret for this token
- **username**: The user whose authentication is represented by this token
- **type**: The type of the token (same as the ``token_type`` column)
- **scope**: A comma-separated list of scope values
- **created**: When the token was created (in seconds since epoch)
- **expires**: When the token expires (in seconds since epoch)

This Redis key will be set to expire when the token expires.

This JSON document is encrypted with `Fernet <https://cryptography.io/en/latest/fernet/>`__ using a key that is private to the authentication system.
This encryption prevents an attacker with access only to the Redis store, but not to the running authentication system or its secrets, from using the Redis keys to reconstruct working tokens.

.. _api:

API
===

Routes
------

All URLs for the REST API for token manipulation start with ``/auth/api/v1``.
This is a sketch of the critical pieces of the API rather than a complete specification.
The full OpenAPI specification of the token API will be maintained as part of the implementation.

In the examples below, the URLs are given as relative URLs.
In a production deployment, they would be fully-qualified ``https`` URLs that include the deployment hostname.

``POST /auth/api/v1/login``
    Used only by the web frontend.
    No data is sent with the request.
    The reply includes the CSRF value to use for all subsequent requests.
    See :ref:`API security <api-security>` for more information.
    Example:

    .. code-block:: json

       {
         csrf: "d56de7d8c6d90cc4a279666156c5923f"
       }

``GET /auth/api/v1/tokens``
    Return all extant tokens.
    This API is limited to administrators.
    Example:

    .. code-block:: json

       [
         {
           "key": "/auth/api/v1/users/alice/tokens/DpBVCadJpTC-uB7NH2TYiQ",
           "username": "alice",
           "token_type": "session",
           "created": 1600723604,
           "last_used": 1600723604,
           "expires": 1600810004,
         },
         {
           "key": "/auth/api/v1/users/alice/tokens/e4uA07XmH5nwkfkPQ1RQFQ",
           "username": "alice",
           "token_type": "notebook",
           "created": 1600723606,
           "expires": 1600810004,
           "parent": "/auth/api/v1/users/alice/tokens/DpBVCadJpTC-uB7NH2TYiQ"
         },
         {
           "key": "/auth/api/v1/users/alice/tokens/N7PClcZ9zzF5xV-KR7vH3w",
           "username": "alice",
           "name": "personal laptop",
           "token_type": "user",
           "scope": "user:read, user:write",
           "created": 1600723681,
           "last_used": 1600723682
         }
       ]

``GET /auth/api/v1/users/{username}/tokens``
    Return all tokens for the given user.
    Only administrators may specify a username other than their own.
    Example:

    .. code-block:: json

       [
         {
           "key": "/auth/api/v1/users/alice/tokens/DpBVCadJpTC-uB7NH2TYiQ",
           "token_type": "session",
           "created": 1600723604,
           "last_used": 1600723604,
           "expires": 1600810004,
         },
         {
           "key": "/auth/api/v1/users/alice/tokens/e4uA07XmH5nwkfkPQ1RQFQ",
           "username": "alice",
           "token_type": "notebook",
           "created": 1600723606,
           "expires": 1600810004,
           "parent": "/auth/api/v1/tokens/DpBVCadJpTC-uB7NH2TYiQ"
         },
         {
           "key": "/auth/api/v1/users/alice/tokens/N7PClcZ9zzF5xV-KR7vH3w",
           "username": "alice",
           "name": "personal laptop",
           "token_type": "user",
           "scope": "user:read, user:write",
           "created": 1600723681,
           "last_used": 1600723682
         }
       ]

``POST /auth/api/v1/users/{username}/tokens``
    Create a new token for the given user.
    Only administrators may specify a username other than their own.
    Only user tokens may be created this way.
    Tokens of other types are created through non-API flows described later.
    The name, scope, and desired expiration are provided as parameters.

``GET /auth/api/v1/users/{username}/tokens/{key}``
    Return the information for a specific token.
    Only administrators may specify a username other than their own.
    Example:

    .. code-block:: json

       {
         "key": "/auth/api/v1/users/alice/tokens/N7PClcZ9zzF5xV-KR7vH3w",
         "username": "alice",
         "name": "personal laptop",
         "token_type": "user",
         "scope": "user:read, user:write",
         "created": 1600723681,
         "expires": 1600727294,
         "last_used": 1600723682
       }

``PATCH /auth/api/v1/users/{username}/tokens/{key}``
    Update data for a token.
    Only administrators may specify a username other than their own.
    Only the ``name``, ``scope``, and ``expires`` properties can be changed.

``DELETE /auth/api/v1/users/{username}/tokens/{key}``
    Revoke a token.
    Only administrators may specify a username other than their own.
    This also revokes all child tokens of that token.

``GET /auth/api/v1/token-info``
    Return information about the provided authentication token.
    (The last used time is nonsensical for this API and is therefore omitted.)
    Example:

    .. code-block:: json

       {
         "key": "/auth/api/v1/users/alice/tokens/N7PClcZ9zzF5xV-KR7vH3w",
         "username": "alice",
         "name": "personal laptop",
         "token_type": "user",
         "scope": "user:read, user:write",
         "created": 1600723681,
         "expires": 1600727294,
         "parent": "/auth/api/v1/users/alice/tokens/DpBVCadJpTC-uB7NH2TYiQ"
       }

``GET /auth/api/v1/users/{username}/token-history``
    Get a history of authentication events for the given user.
    Only administrators may specify a username other than their own.
    The range of events can be controlled by pagination parameters included in the URL:

    - ``offset``: Skip the first N elements
    - ``limit``: Return only N elements
    - ``since``: Return only events after this timestamp
    - ``until``: Return only events until this timestamp
    - ``key``: Limit to authentications involving the given key (including child tokens of that key)
    - ``token_type``: Limit to authentications with the given token type

    Example:

    .. code-block:: json

       [
         {
           "key": "/auth/api/v1/users/alice/tokens/DpBVCadJpTC-uB7NH2TYiQ",
           "token_type": "session",
           "ip_address": "192.88.99.2",
           "when": 1600725470
         },
         {
           "key": "/auth/api/v1/users/alice/tokens/e4uA07XmH5nwkfkPQ1RQFQ",
           "parent": "/auth/api/v1/users/alice/tokens/DpBVCadJpTC-uB7NH2TYiQ",
           "token_type": "notebook",
           "when": 1600725676
         },
         {
           "key": "/auth/api/v1/users/alice/tokens/N7PClcZ9zzF5xV-KR7vH3w",
           "name": "personal laptop",
           "token_type": "user",
           "scope": "user:read, user:write",
           "ip_address": "2001:0db8:85a3:0000:0000:8a2e:0370:7334",
           "when": 1600725767
         }
       ]

    Available history will be limited by the granularity of history event storage.
    For example, multiple web accesses in a short period of time may be aggregated into a single authentication event.

.. _api-security:

Security
--------

API calls may be authenticated one of two ways: by providing a token in an ``Authorization`` header with type ``bearer``, or by sending a session cookie.
The session cookie method will be used by :ref:`web frontends <web>`.
Direct API calls will use the ``Authorization`` header.

All API ``POST``, ``PATCH``, or ``DELETE`` calls authenticated via session cookie must include an ``X-CSRF-Token`` header in the request.
The value of this header is obtained via the ``/auth/api/v1/login`` route.
This value will be checked by the server against the CSRF token included in the session referenced by the session cookie.
Direct API calls authenticating with the ``Authorization`` header can ignore this requirement.

This API does not support cross-origin requests.
It therefore should respond with an error to ``OPTIONS`` requests.

.. _web:

Web UI
======

The web interface will be written in React_ using Gatsby_ and styled-components_.
The frontend will use the :ref:`same API <api>` as API clients to retrieve and change data.

.. _React: https://reactjs.org/
.. _Gatsby: https://www.gatsbyjs.com/
.. _styled-components: https://styled-components.com/

User interface
--------------

General users will have access to the following pages:

Token list
    Lists all of the unexpired tokens for the current user.
    The token list is divided into separate sections for web sessions, user-created tokens, and notebook tokens, with internal tokens shown under their parent tokens.
    The last-used time is shown with each token, rendered as a human-readable delta from the current time (for example, "10 minutes ago" or "1 month ago") with a more accurate timestamp available via mouseover or some other interface.
    From this list the user can revoke any token.

View a specific token
    Shows the details for a single token, including its authentication history.
    The user can also revoke the token from this page.

Create new token
    Creates a new user token and displays the full token (including the secret) to the user once.
    The user can select a name, list of scopes (chosen from a selection list), and optional expiration.
    The optional expiration should offer a standard selection of reasonable lengths of time as well as allow the user to enter their own.

Modify a token
    Allows the user to modify the name, scope, or expiration date of an existing token.

Token authentication history
    Shows a paginated list of token authentication events for the user, divided into web sessions, user-created tokens, notebook tokens, and internal tokens.
    The user can limit by token type or date, or click on a token to see its details and the authentication events relevant to it.

Admin interface
---------------

Any admin user can impersonate a user and see the same pages that user would see.
When this is happening, every page displays a banner indicating that impersonation is being done and identifying the actual user.

Admin users also have access to two additional pages:

Admin token list
    Lists (with pagination) all of the current-valid tokens known to the system.
    Allows restricting the view by token types and users.

Admin token view
    Shows the details of any single token, including its authentication history.
    The token can be revoked from this page.

Admin token authentication history
    Shows a paginated list of all recent token authentication events.
    Allows restricting by IP address pattern, token types, users, and date range.
