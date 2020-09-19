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
       name       VARCHAR(64)  NOT NULL,
       username   VARCHAR(64)  NOT NULL,
       token_type token_enum   NOT NULL,
       scope      VARCHAR(256) NOT NULL,
       created    TIMESTAMP    NOT NULL,
       expires    TIMESTAMP
   );
   CREATE INDEX tokens_by_username ON tokens (username, name);

Internal tokens are derived from non-internal tokens.
That relationship is captured by the following schema:

.. code-block:: sql

   CREATE TABLE subtokens (
       PRIMARY KEY (id),
       id     SERIAL      NOT NULL,
       parent VARCHAR(64)          REFERENCES tokens ON DELETE SET NULL,
       child  VARCHAR(64) NOT NULL REFERENCES tokens ON DELETE CASCADE,
       scope VARCHAR(256) NOT NULL
   );
   CREATE INDEX subtokens_by_scope ON subtokens (parent, scope);

Finally, token usage information is stored in a history table.
This will not hold every usage, since that data could be overwhelming for web sessions and other instances of high-frequency calls.
However, it will attempt to capture the most recent uses from a given IP address.

This table stores data even for tokens that have been deleted, so it duplicates some information from the ``tokens`` table rather than adding a foreign key.

It doubles as the web session history table, since web sessions are another type of token.

.. code-block:: sql

   CREATE TABLE token_authentications (
       PRIMARY KEY (id),
       id         SERIAL       NOT NULL,
       key        VARCHAR(64)  NOT NULL,
       name       VARCHAR(64)  NOT NULL,
       username   VARCHAR(64)  NOT NULL,
       token_type token_enum   NOT NULL,
       scope      VARCHAR(256) NOT NULL,
       ip_address VARCHAR(64),
       when       TIMESTAMP    NOT NULL
   );
   CREATE INDEX token_authentications_by_username (username, when);

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
This encryption prevents an attacker with access only to the Redis store but not to the running authentication system of its secrets using the Redis keys to reconstruct working tokens.
