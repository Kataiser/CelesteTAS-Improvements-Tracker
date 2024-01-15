import datetime
import logging
import time
from typing import Optional

import jwt
import requests
import ujson

import db
import utils
from utils import plural


def generate_jwt(min_time: int) -> str:
    current_time = time.time()
    cached_jwt = get_token_cache('_jwt')

    if cached_jwt:
        time_remaining = cached_jwt[1] - current_time

        if time_remaining > min_time:
            log.info(f"Reused JWT with {round(time_remaining / 60, 1)} mins remaining")
            return cached_jwt[0]

    with open('celestetas-improvements-tracker.2022-05-01.private-key.pem', 'rb') as pem_file:
        private = pem_file.read()

    payload = {'iat': round(current_time - 60),
               'exp': round(current_time + (9.5 * 60)),
               'iss': '196447'}

    generated_jwt = jwt.encode(payload, private, algorithm='RS256')
    log.info("Generated JWT")
    set_token_cache('_jwt', (generated_jwt, payload['exp']))
    return generated_jwt


def generate_access_token(installation_owner: str, min_jwt_time: int) -> tuple:
    headers = {'Authorization': f'Bearer {generate_jwt(min_jwt_time)}', 'Accept': 'application/vnd.github.v3+json'}
    installations_saved = {}

    try:
        installations_saved[installation_owner] = db.installations.get(installation_owner, consistent_read=False)
    except db.DBKeyError:
        log.info(f"Installation ID not cached for owner \"{installation_owner}\"")
        r = requests.get('https://api.github.com/app/installations', headers=headers)
        utils.handle_potential_request_error(r, 200)
        installations = ujson.loads(r.content)
        log.info(f"Found {len(installations)} installation{plural(installations)}: {[(i['id'], i['account']['login'], i['created_at']) for i in installations]}")

        for installation in installations:
            installations_saved[installation['account']['login']] = installation['id']
            db.installations.set(installation['account']['login'], installation['id'])

    if installation_owner in installations_saved:
        installation_id = installations_saved[installation_owner]
    else:
        raise InstallationOwnerMissingError(installation_owner)

    r = requests.post(f'https://api.github.com/app/installations/{installation_id}/access_tokens', headers=headers)
    utils.handle_potential_request_error(r, 201)
    access_token_data = ujson.loads(r.content)
    token_expiration_str = access_token_data['expires_at'][:-1]
    token_expiration = datetime.datetime.fromisoformat(f'{token_expiration_str}+00:00')
    log.info(f"Generated {installation_owner} access token: {access_token_data}")
    return access_token_data['token'], token_expiration.timestamp()


def access_token(installation_owner: str, min_time: int):
    token = get_token_cache(installation_owner)

    if not token:
        token = generate_access_token(installation_owner, min_time)
        set_token_cache(installation_owner, token)
    else:
        time_remaining = token[1] - time.time()

        if time_remaining > min_time:
            log.info(f"Reusing {installation_owner} access token with {round(time_remaining / 60, 1)} mins remaining")
        else:
            token = generate_access_token(installation_owner, min_time)
            set_token_cache(installation_owner, token)

    return token[0]


def get_token_cache(key: str) -> Optional[tuple]:
    if key in tokens_local:
        return tokens_local[key]

    try:
        token = db.tokens.get(key, consistent_read=False)
        token[1] = int(token[1])
        tokens_local[key] = token
        return token
    except db.DBKeyError:
        return


def set_token_cache(key: str, token: tuple):
    tokens_local[key] = token
    db.tokens.set(key, (token[0], round(token[1])))


class InstallationOwnerMissingError(Exception):
    pass


tokens_local = {}
log: Optional[logging.Logger] = None


if __name__ == '__main__':
    generate_access_token('Kataiser', 30)
