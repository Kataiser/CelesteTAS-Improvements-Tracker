import functools
import logging
import time
from typing import Optional

import jwt
import requests
from discord.ext import tasks

import utils
from utils import plural


def generate_jwt() -> str:
    with open('celestetas-improvements-tracker.2022-05-01.private-key.pem', 'rb') as pem_file:
        private = pem_file.read()

    current_time = time.time()
    payload = {'iat': round(current_time - 60),
               'exp': round(current_time + (9.75 * 60)),
               'iss': '196447'}

    generated_jwt = jwt.encode(payload, private, algorithm='RS256')
    log.info(f"Generated JWT: {generated_jwt}")
    return generated_jwt


@functools.cache
def get_access_token(installation_owner: str) -> str:
    headers = {'Authorization': f'Bearer {generate_jwt()}', 'Accept': 'application/vnd.github.v3+json'}
    r = requests.get('https://api.github.com/app/installations', headers=headers)
    utils.handle_potential_request_error(r, 200)
    installations = r.json()
    log.info(f"Found {len(installations)} installation{plural(installations)}: {[(i['id'], i['account']['login'], i['created_at']) for i in installations]}")

    for installation in installations:
        if installation['account']['login'] == installation_owner:
            r = requests.post(installation['access_tokens_url'], headers=headers)
            utils.handle_potential_request_error(r, 201)
            access_token_data = r.json()
            log.info(f"Generated access token: {access_token_data}")
            return access_token_data['token']


# JWT only lasts like 10 mins, so clear tokens occasionally
@tasks.loop(seconds=(9.5 * 60))
async def reset_token():
    cache_size = get_access_token.cache_info().currsize

    if cache_size:
        get_access_token.cache_clear()
        log.info(f"Cleared {cache_size} access token{plural(cache_size)}")


log: Optional[logging.Logger] = None

if __name__ == '__main__':
    get_access_token()
    print(get_access_token.cache_info())
    get_access_token.cache_clear()
    print(get_access_token.cache_info())
