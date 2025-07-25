import datetime
import logging
import os
import time
from typing import Union

import dotenv
import jwt
import niquests
import orjson

import db
import utils
from constants import github_app_id
from utils import plural


def generate_jwt(min_time: int) -> str:
    current_time = time.time()
    cached_jwt, time_remaining = get_cached_token('_jwt', min_time)

    if cached_jwt:
        log.info(f"Reused JWT with {round(time_remaining / 60, 1)} mins remaining")
        return cached_jwt[0]

    payload = {'iat': round(current_time - 60),
               'exp': round(current_time + (9.5 * 60)),
               'iss': github_app_id}

    dotenv.load_dotenv()
    generated_jwt = jwt.encode(payload, os.getenv('GITHUB_PRIVATE_KEY'), algorithm='RS256')
    log.info("Generated JWT")
    set_token_cache('_jwt', [generated_jwt, payload['exp']])
    return generated_jwt


def generate_access_token(installation_owner: str, min_jwt_time: int) -> tuple:
    headers = {'Authorization': f'Bearer {generate_jwt(min_jwt_time)}', 'Accept': 'application/vnd.github.v3+json'}
    installations_saved = {}

    try:
        installations_saved[installation_owner] = db.installations.get(installation_owner, consistent_read=False)
    except db.DBKeyError:
        log.info(f"Installation ID not cached for owner \"{installation_owner}\"")
        r = niquests.get('https://api.github.com/app/installations', headers=headers, timeout=30)
        utils.handle_potential_request_error(r, 200)
        installations = orjson.loads(r.content)
        log.info(f"Found {len(installations)} installation{plural(installations)}: {[(i['id'], i['account']['login'], i['created_at']) for i in installations]}")

        for installation in installations:
            installations_saved[installation['account']['login']] = installation['id']
            db.installations.set(installation['account']['login'], installation['id'])

    if installation_owner in installations_saved:
        installation_id = installations_saved[installation_owner]
    else:
        raise InstallationOwnerMissingError(installation_owner)

    r = niquests.post(f'https://api.github.com/app/installations/{installation_id}/access_tokens', headers=headers, timeout=30)
    utils.handle_potential_request_error(r, 201)
    access_token_data = orjson.loads(r.content)
    token_expiration_str = access_token_data['expires_at'][:-1]
    token_expiration = datetime.datetime.fromisoformat(f'{token_expiration_str}+00:00')
    log.info(f"Generated {installation_owner} access token: {access_token_data}")
    return access_token_data['token'], token_expiration.timestamp()


def access_token(installation_owner: str, min_time: int):
    token, time_remaining = get_cached_token(installation_owner, min_time)

    if not token:
        token = generate_access_token(installation_owner, min_time)
        set_token_cache(installation_owner, token)
    else:
        log.info(f"Reusing {installation_owner} access token with {round(time_remaining / 60, 1)} mins remaining")

    return token[0]


def get_cached_token(key: str, min_time: int) -> tuple[list, int]:
    if key in tokens_local:
        time_remaining = tokens_local[key][1] - time.time()

        if time_remaining > min_time:
            return tokens_local[key], time_remaining

    try:
        token = db.tokens.get(key, consistent_read=False)
        token[1] = int(token[1])
        time_remaining = token[1] - time.time()

        if time_remaining > min_time:
            tokens_local[key] = token
            return token, time_remaining
    except db.DBKeyError:
        pass

    return [], 0


def set_token_cache(key: str, token: Union[tuple, list]):
    token = list(token)
    token[1] = round(token[1])
    tokens_local[key] = token
    db.tokens.set(key, token)


class InstallationOwnerMissingError(Exception):
    pass


tokens_local = {}
log: Union[logging.Logger, utils.LogPlaceholder] = utils.LogPlaceholder()


if __name__ == '__main__':
    generate_access_token('Kataiser', 30)
