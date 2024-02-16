[![Github Actions CI](https://img.shields.io/github/actions/workflow/status/Kataiser/CelesteTAS-Improvements-Tracker/Tests.yml?label=tests)](https://github.com/Kataiser/CelesteTAS-Improvements-Tracker/actions/workflows/Tests.yml)
[![Codecov](https://codecov.io/gh/Kataiser/CelesteTAS-Improvements-Tracker/graph/badge.svg?token=64TF2SXS9F)](https://codecov.io/gh/Kataiser/CelesteTAS-Improvements-Tracker)
[![Codacy Badge](https://app.codacy.com/project/badge/Grade/1d05fa6351624fc9af5385bfff1c263e)](https://app.codacy.com/gh/Kataiser/CelesteTAS-Improvements-Tracker/dashboard?utm_source=gh&utm_medium=referral&utm_content=&utm_campaign=Badge_grade)

# CelesteTAS-Improvements-Tracker
![avatar](improvements%20bot%20avatar.png)


## Development setup

1. **AWS Credentials**

   Create an AWS IAM account at https://console.aws.amazon.com/ with the policy `AmazonDynamoDBFullAccess` and create an access key, then run
   ```shell
   aws configure
   ```
   with the created access key, and set your preferred default region.
2. **Create the following dynamodb tables**

   | Table name                                     | Key                  | Key type |
   |------------------------------------------------|----------------------|----------|
   | `CelesteTAS-Improvement-Tracker_githubs`       | `discord_id`         | Number   |
   | `CelesteTAS-Improvement-Tracker_history_log`   | `timestamp`          | String   |
   | `CelesteTAS-Improvement-Tracker_installations` | `github_username`    | String   |
   | `CelesteTAS-Improvement-Tracker_project_logs`  | `project_id`         | Number   |
   | `CelesteTAS-Improvement-Tracker_sheet_writes`  | `timestamp`          | String   |
   | `CelesteTAS-Improvement-Tracker_logs`          | `time`               | String   |
   | `CelesteTAS-Improvement-Tracker_sync_results`  | `project_id`         | Number   |
   | `CelesteTAS-Improvement-Tracker_misc`          | `key`                | String   |
   | `CelesteTAS-Improvement-Tracker_contributors`  | `project_id`         | Number   |
   | `CelesteTAS-Improvement-Tracker_sid_caches`    | `project_id`         | Number   |
   | `CelesteTAS-Improvement-Tracker_tokens`        | `installation_owner` | String   |
   | `CelesteTAS-Improvement-Tracker_projects`      | `project_id`         | Number   |
   | `CelesteTAS-Improvement-Tracker_path_caches`   | `project_id`         | Number   |

3. **Discord**

   Create a new application at https://discord.com/developers/, go to `Bot` and click `Reset Token`. Write the token into the `bot_token` file.
   You can invite the bot with this url: https://discord.com/api/oauth2/authorize?client_id=<your_oauth_client_id>&permissions=2147493888&scope=bot
4. **Google**

   Follow https://cloud.google.com/iam/docs/keys-create-delete to create a service account with a JSON key, download the json file into `service.json`.
5. **Github**

   Create a new GitHub app at https://github.com/settings/apps/new, add repo permissions and generate a private key, download the key into `celestetas-improvements-tracker.2022-05-01.private-key.pem`. Change the `github_app_id`  in `gen_token.py` to your generated app id.
6. **Change hardcoded constants**

   Change `admin_user_id` in `constants.py` to your discord account id and `slash_command_servers` to some test discord server