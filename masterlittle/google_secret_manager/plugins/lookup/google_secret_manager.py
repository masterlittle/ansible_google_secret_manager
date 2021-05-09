# Copyright: (c) 2018, Aaron Smith <ajsmith10381@gmail.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)

__metaclass__ = type


DOCUMENTATION = r'''
lookup: aws_secret
author:
  - Aaron Smith <ajsmith10381@gmail.com>
requirements:
  - boto3
  - botocore>=1.10.0
extends_documentation_fragment:
- amazon.aws.aws_credentials
- amazon.aws.aws_region

short_description: Look up secrets stored in AWS Secrets Manager.
description:
  - Look up secrets stored in AWS Secrets Manager provided the caller
    has the appropriate permissions to read the secret.
  - Lookup is based on the secret's I(Name) value.
  - Optional parameters can be passed into this lookup; I(version_id) and I(version_stage)
options:
  _terms:
    description: Name of the secret to look up in AWS Secrets Manager.
    required: True
  bypath:
    description: A boolean to indicate whether the parameter is provided as a hierarchy.
    default: false
    type: boolean
    version_added: 1.4.0
  nested:
    description: A boolean to indicate the secret contains nested values.
    type: boolean
    default: false
    version_added: 1.4.0
  version_id:
    description: Version of the secret(s).
    required: False
  version_stage:
    description: Stage of the secret version.
    required: False
  join:
    description:
        - Join two or more entries to form an extended secret.
        - This is useful for overcoming the 4096 character limit imposed by AWS.
        - No effect when used with I(bypath).
    type: boolean
    default: false
  on_missing:
    description:
        - Action to take if the secret is missing.
        - C(error) will raise a fatal error when the secret is missing.
        - C(skip) will silently ignore the missing secret.
        - C(warn) will skip over the missing secret but issue a warning.
    default: error
    type: string
    choices: ['error', 'skip', 'warn']
  on_denied:
    description:
        - Action to take if access to the secret is denied.
        - C(error) will raise a fatal error when access to the secret is denied.
        - C(skip) will silently ignore the denied secret.
        - C(warn) will skip over the denied secret but issue a warning.
    default: error
    type: string
    choices: ['error', 'skip', 'warn']
'''

EXAMPLES = r"""
 - name: lookup secretsmanager secret in the current region
   debug: msg="{{ lookup('amazon.aws.aws_secret', '/path/to/secrets', bypath=true) }}"

 - name: Create RDS instance with aws_secret lookup for password param
   rds:
     command: create
     instance_name: app-db
     db_engine: MySQL
     size: 10
     instance_type: db.m1.small
     username: dbadmin
     password: "{{ lookup('amazon.aws.aws_secret', 'DbSecret') }}"
     tags:
       Environment: staging

 - name: skip if secret does not exist
   debug: msg="{{ lookup('amazon.aws.aws_secret', 'secret-not-exist', on_missing='skip')}}"

 - name: warn if access to the secret is denied
   debug: msg="{{ lookup('amazon.aws.aws_secret', 'secret-denied', on_denied='warn')}}"

 - name: lookup secretsmanager secret in the current region using the nested feature
   debug: msg="{{ lookup('amazon.aws.aws_secret', 'secrets.environments.production.password', nested=true) }}"
   # The secret can be queried using the following syntax: `aws_secret_object_name.key1.key2.key3`.
   # If an object is of the form `{"key1":{"key2":{"key3":1}}}` the query would return the value `1`.
"""

RETURN = r"""
_raw:
  description:
    Returns the value of the secret stored in AWS Secrets Manager.
"""

import json

try:
    from google.cloud import secretmanager
    from google.cloud.secretmanager_v1 import SecretManagerServiceClient
    from google.api_core.exceptions import NotFound as NF
    from google.api_core.exceptions import PermissionDenied as PD
    from google.api_core.exceptions import ClientError as CE

except ImportError as e:
    raise e # will be captured by imported HAS_BOTO3

from ansible.errors import AnsibleError
from ansible.module_utils.six import string_types
from ansible.module_utils._text import to_native
from ansible.plugins.lookup import LookupBase


class LookupModule(LookupBase):
    def run(self, project_id, terms, variables=None, nested=False, join=False, version_id="latest", on_missing='error',
            on_denied='error'):
        '''
                   :arg terms: a list of lookups to run.
                       e.g. ['parameter_name', 'parameter_name_too' ]
                   :kwarg variables: ansible variables active at the time of the lookup
                   :kwarg aws_secret_key: identity of the AWS key to use
                   :kwarg aws_access_key: AWS secret key (matching identity)
                   :kwarg aws_security_token: AWS session key if using STS
                   :kwarg decrypt: Set to True to get decrypted parameters
                   :kwarg region: AWS region in which to do the lookup
                   :kwarg bypath: Set to True to do a lookup of variables under a path
                   :kwarg nested: Set to True to do a lookup of nested secrets
                   :kwarg join: Join two or more entries to form an extended secret
                   :kwarg version_stage: Stage of the secret version
                   :kwarg version_id: Version of the secret(s)
                   :kwarg on_missing: Action to take if the secret is missing
                   :kwarg on_denied: Action to take if access to the secret is denied
                   :returns: A list of parameter values or a list of dictionaries if bypath=True.
               '''
        missing = on_missing.lower()
        if not isinstance(missing, string_types) or missing not in ['error', 'warn', 'skip']:
            raise AnsibleError('"on_missing" must be a string and one of "error", "warn" or "skip", not %s' % missing)

        denied = on_denied.lower()
        if not isinstance(denied, string_types) or denied not in ['error', 'warn', 'skip']:
            raise AnsibleError('"on_denied" must be a string and one of "error", "warn" or "skip", not %s' % denied)

        client = secretmanager.SecretManagerServiceClient()

        secrets = []
        for term in terms:
            value = self.get_secret_value(term, client, project_id, version_id=version_id,
                                          on_missing=missing, on_denied=denied, nested=nested)
            if value:
                secrets.append(value)
        if join:
            joined_secret = []
            joined_secret.append(''.join(secrets))
            return joined_secret

        return secrets

    def get_secret_value(self, term, client: SecretManagerServiceClient, project_id, version_id,
                         on_missing=None, on_denied=None, nested=False):
        parent = f"projects/{project_id}/secrets"
        name = f"{parent}/{term}/versions"
        if nested:
            if len(term.split('.')) < 2:
                raise AnsibleError("Nested query must use the following syntax: `aws_secret_name.<key_name>.<key_name>")
            secret_name = term.split('.')[0]
            name = f"{parent}/{secret_name}/versions"
        if version_id:
            name = f"{name}/{version_id}"
        else:
            name = f"{name}/latest"

        try:
            response = client.access_secret_version(request={"name": name})
            payload = response.payload.data.decode("UTF-8")

            if nested:
                query = term.split('.')[1:]
                secret_string = json.loads(payload)
                ret_val = secret_string
                for key in query:
                    if key in ret_val:
                        ret_val = ret_val[key]
                    else:
                        raise AnsibleError(
                            "Successfully retrieved secret but there exists no key {0} in the secret".format(key))
                return str(ret_val)
            else:
                return payload
        except NF:
            if on_missing == 'error':
                raise AnsibleError("Failed to find secret %s (ResourceNotFound)" % term)
            elif on_missing == 'warn':
                self._display.warning('Skipping, did not find secret %s' % term)
        except PD:  # pylint: disable=duplicate-except
            if on_denied == 'error':
                raise AnsibleError("Failed to access secret %s (AccessDenied)" % term)
            elif on_denied == 'warn':
                self._display.warning('Skipping, access denied for secret %s' % term)
        except (
                CE) as exc:  # pylint: disable=duplicate-except
            raise AnsibleError("Failed to retrieve secret: %s" % to_native(exc))

        return None