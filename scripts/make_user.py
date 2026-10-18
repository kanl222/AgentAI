"""Print configuration record; secret is entered privately and never echoed."""
import getpass
import hashlib
import json
import argparse
parser = argparse.ArgumentParser()
parser.add_argument('name')
parser.add_argument('--roles', default='reader')
parser.add_argument('--sources', required=True)
args = parser.parse_args()
secret = getpass.getpass('API key (at least 32 random characters): ')
if len(secret)<32:
    parser.error('Use at least 32 random characters')
print(json.dumps({'name':args.name,'sha256':hashlib.sha256(secret.encode()).hexdigest(),
                  'roles':args.roles.split(','),'sources':args.sources.split(',')}))
