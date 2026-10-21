"""Authentication and authorization, independent of HTTP transport."""
import hashlib
import hmac
import json
import os
import re
from ..errors import ApplicationError

def authenticate(header):
    if not header or not header.startswith('Bearer '):
        raise ApplicationError(401, 'Bearer key required')
    digest = hashlib.sha256(header[7:].encode()).hexdigest()
    try:
        users = json.loads(os.getenv('AGENT_USERS', '[]'))
        if not isinstance(users, list):
            raise ValueError('Expected users array')
        for entry in users:
            if (not isinstance(entry, dict)
                    or not isinstance(entry.get('name'), str)
                    or not isinstance(entry.get('sha256'), str)
                    or not re.fullmatch(r'[a-f0-9]{64}', entry['sha256'])
                    or not all(isinstance(entry.get(field), list)
                               and all(isinstance(value, str) for value in entry[field])
                               for field in ('roles', 'sources'))):
                raise ValueError('Invalid user record')
    except (ValueError, TypeError):
        raise ApplicationError(503, 'Invalid AGENT_USERS configuration; contact the administrator') from None
    for user in users:
        if hmac.compare_digest(digest, user['sha256']):
            return user
    raise ApplicationError(401, 'Invalid key')


def require(user, role, source=None):
    if role not in user['roles'] or (source is not None and source not in user['sources']):
        raise ApplicationError(403, 'Access denied')
