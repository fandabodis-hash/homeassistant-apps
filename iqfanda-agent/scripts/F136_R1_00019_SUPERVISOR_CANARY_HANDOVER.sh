#!/usr/bin/env bash
# F136 R1 | TNG IQ FANDA 00019 | one running Agent, guarded handover 0.1.176 -> canary 0.1.177.
# Run ONLY inside the Home Assistant 00019 Terminal & SSH add-on with Supervisor manager access.
# No VPS writes, no DB operations, no Modbus frames, no Zigbee actions, no global Git main changes.
set -Eeuo pipefail
umask 077
export LC_ALL=C
MODE="${1:---audit}"
case "$MODE" in --audit|--apply|--status|--rollback) ;; *) echo 'Usage: bash F136_R1_00019_SUPERVISOR_CANARY_HANDOVER.sh --audit|--apply|--status|--rollback'; exit 2;; esac
ORIGINAL_REPO='https://github.com/fandabodis-hash/homeassistant-apps'
CANARY_REPO='https://github.com/fandabodis-hash/homeassistant-apps#canary/f135-00019-agent-0.1.177-20261123'
EXPECTED_UUID='648d4229-7c17-400f-bbed-ffe5ab04b844'
EXPECTED_SITE='980e5e5e-76b4-4cac-9b6c-1f31fa5fd6e1'
EXPECTED_SERIAL='F800711-TNG-00019'
CANARY_ID="$(printf %s "$CANARY_REPO" | sha1sum | cut -c1-8)_tng_iq_fanda_agent"
VERSION_OLD='0.1.176'; VERSION_NEW='0.1.177'
STAMP="$(date +%Y%m%d-%H%M%S)"
ROOT_CONFIGS='/addon_configs'; [[ -d "$ROOT_CONFIGS" ]] || ROOT_CONFIGS='/app_configs'
SUPERVISOR_URL='http://supervisor'
LOG_ROOT='/share'
for c in jq curl sha1sum sha256sum find cp date tee wc du awk sed grep tr sleep; do command -v "$c" >/dev/null || { echo "F136_STOP|REQUIRED_BINARY_MISSING_$c"; exit 3; }; done
[[ -n "${SUPERVISOR_TOKEN:-}" ]] || { echo 'F136_STOP|SUPERVISOR_TOKEN_UNAVAILABLE_RUN_INSIDE_AUTHORIZED_HA_TERMINAL'; exit 3; }
[[ -d "$ROOT_CONFIGS" ]] || { echo 'F136_STOP|APP_CONFIG_FOLDERS_NOT_MAPPED_RUN_TERMINAL_WITH_ALL_ADDON_CONFIGS_ACCESS'; exit 3; }
[[ -d "$LOG_ROOT" && -w "$LOG_ROOT" ]] || { echo 'F136_STOP|SHARE_FOLDER_NOT_ACCESSIBLE'; exit 3; }
STATE_PATH="$LOG_ROOT/F136_00019_CANARY_HANDOVER_STATE.json"
LOG_PATH="$LOG_ROOT/F136_R1_00019_${MODE#--}_${STAMP}.txt"
exec > >(tee -a "$LOG_PATH") 2>&1
printf 'F136_R1|MODE|%s\nF136_R1|TARGET|%s\nF136_R1|CANARY_REPO|%s\nF136_R1|REPORT|%s\n' "$MODE" "$EXPECTED_SERIAL" "$CANARY_REPO" "$LOG_PATH"
# Never print Supervisor credentials, addon options, device tokens, HTTP bodies or local device.json.
fail(){
  echo "F136_STOP|$1"
  if [[ "${switched:-0}" == 1 && -n "${old_slug:-}" ]]; then
    trap - ERR
    rollback_internal "FAIL_$1" || echo 'F136_CRITICAL|AUTOMATIC_ROLLBACK_FAILED_MANUAL_REVIEW_REQUIRED'
    switched=0
  fi
  exit 4
}
api(){
  local method="$1" endpoint="$2" body="${3:-}" timeout="${4:-50}" response
  if [[ "$method" == 'GET' ]]; then
    response="$(curl --silent --show-error --fail --connect-timeout 7 --max-time "$timeout" -H "Authorization: Bearer $SUPERVISOR_TOKEN" -H 'Accept: application/json' "$SUPERVISOR_URL$endpoint")" || { echo "F136_HTTP_FAILED|$method|$endpoint" >&2; return 12; }
  else
    response="$(printf %s "$body" | curl --silent --show-error --fail --connect-timeout 7 --max-time "$timeout" -H "Authorization: Bearer $SUPERVISOR_TOKEN" -H 'Accept: application/json' -H 'Content-Type: application/json' --data-binary @- -X "$method" "$SUPERVISOR_URL$endpoint")" || { echo "F136_HTTP_FAILED|$method|$endpoint" >&2; return 12; }
  fi
  printf '%s' "$response" | jq --exit-status '.result=="ok"' >/dev/null || { echo "F136_SUPERVISOR_RESULT_ERROR|$method|$endpoint" >&2; return 13; }
  printf '%s' "$response"
}
app_info(){ api GET "/addons/$1/info"; }
app_state(){ app_info "$1" | jq -r '.data.state // "unknown"'; }
wait_state(){
  local slug="$1" wanted="$2" round state
  for ((round=0;round<24;round++)); do
    state="$(app_state "$slug")" || return 1
    [[ "$state" == "$wanted" ]] && return 0
    sleep 2
  done
  echo "F136_STATE_MISMATCH|$slug|wanted=$wanted|got=$state"
  return 1
}
identity_guard(){
  local old="$1" folder j uuid site serial
  folder="$ROOT_CONFIGS/$old"
  [[ -d "$folder" && -f "$folder/device.json" ]] || fail 'ORIGINAL_DEVICE_JSON_UNAVAILABLE_IN_APP_CONFIG'
  [[ ! -L "$folder" && ! -L "$folder/device.json" ]] || fail 'ORIGINAL_CONFIG_SYMLINK_NOT_ALLOWED'
  j="$(jq -c . "$folder/device.json" 2>/dev/null)" || fail 'ORIGINAL_DEVICE_JSON_INVALID'
  uuid="$(jq -r '.device_uuid // ""' <<<"$j")"
  site="$(jq -r '.site_id // ""' <<<"$j")"
  serial="$(jq -r '.serial_number // .device_serial_number // ""' <<<"$j")"
  [[ "$uuid" == "$EXPECTED_UUID" ]] || fail 'ONLY_EXACT_00019_UUID_ALLOWED'
  [[ -z "$site" || "$site" == "$EXPECTED_SITE" ]] || fail '00019_SITE_ID_MISMATCH'
  [[ -z "$serial" || "$serial" == "$EXPECTED_SERIAL" ]] || fail '00019_SERIAL_MISMATCH'
  [[ -n "$(jq -r '.device_token // ""' <<<"$j")" ]] || fail 'DEVICE_TOKEN_MISSING'
  if find "$folder" -type l -print -quit | grep -q .; then fail 'ORIGINAL_APP_CONFIG_CONTAINS_SYMLINK'; fi
  echo 'F136_IDENTITY|PASS_EXACT_00019_UUID_AND_DEVICE_TOKEN_EXISTS'
}
original_slug(){
  local response found count
  response="$(api GET '/addons')" || fail 'CANNOT_LIST_INSTALLED_APPS'
  found="$(jq -r --arg v "$VERSION_OLD" '[.data.addons[]? | select(.name=="TNG IQ FANDA Agent" and .version==$v and (.slug|endswith("_tng_iq_fanda_agent"))) | .slug] | .[]' <<<"$response")"
  count="$(printf '%s\n' "$found" | sed '/^$/d' | wc -l | tr -d ' ')"
  [[ "$count" == 1 ]] || fail "EXPECTED_ONE_INSTALLED_176_AGENT_FOUND_$count"
  printf '%s' "$found"
}
repo_config_check(){
  local base='https://raw.githubusercontent.com/fandabodis-hash/homeassistant-apps/canary/f135-00019-agent-0.1.177-20261123/iqfanda-agent'
  local path expected data n actual
  while IFS='|' read -r path expected; do
    [[ -n "$path" ]] || continue
    data="$(curl --silent --show-error --fail --location --connect-timeout 10 --max-time 55 "$base/$path")" || fail "CANARY_GITHUB_FILE_UNAVAILABLE_$path"
    [[ -n "$data" ]] || fail "CANARY_GITHUB_EMPTY_FILE_$path"
    if [[ "$path" == 'config.yaml' ]]; then
      grep -q '^version: "0.1.177"' <<<"$data" || fail 'CANARY_SOURCE_VERSION_WRONG'
      grep -q '^boot: manual' <<<"$data" || fail 'CANARY_MUST_HAVE_BOOT_MANUAL'
    fi
    if [[ "$path" == 'rootfs/app/communication/midea_00019_cloud_probe.py' ]]; then
      grep -q "$EXPECTED_UUID" <<<"$data" || fail 'CANARY_MIDEA_UUID_MISSING'
    fi
    # Exact git blob hash = SHA1("blob <byte_count>\0" + exact content).
    # Pinned eight agent files, including the entire original worker/heartbeat.
    n="$(printf '%s\n' "$data" | wc -c | tr -d ' ')"
    actual="$( { printf 'blob %s\0' "$n"; printf '%s\n' "$data"; } | sha1sum | cut -d' ' -f1)"
    [[ "$actual" == "$expected" ]] || fail "CANARY_PINNED_SOURCE_BLOB_DRIFT_$path"
  done <<'F136_SOURCE_PINS'
config.yaml|7d69edfe69b9d624a308730790ba32446a50bd6b
rootfs/app/VERSION|dfb8b6438c1affd001ecde64fea1a22504917731
Dockerfile|33aaa1f13f69524d5ab28204c76f80255714293f
rootfs/app/command_worker.py|d97079f5d9162612b42482a8ed5c2714c99067c4
rootfs/app/heartbeat.py|f89f14f7b6811df0b424fc7949e5ca60bfe3b97f
rootfs/app/communication/midea_00019_readonly.py|2374722be173dd3335ef84f34af83e2d9e891d9e
rootfs/app/communication/midea_00019_cloud_probe.py|c013312d22f027deef226456c85a2f7526c6356a
rootfs/app/communication/midea_00019_ftdi_inventory.py|fed614d8a8f2763248aacfe9ffeb9dd654797f8d
F136_SOURCE_PINS
  echo 'F136_CANARY_GITHUB|PASS_ALL_8_PINNED_AGENT_SOURCES_BOOT_MANUAL_AND_MIDEA_READONLY'
}

old_boot='manual'; old_autoupdate='false'; old_slug=''; new_slug="$CANARY_ID"; switched=0
rollback_internal(){
  local why="$1"
  echo "F136_ROLLBACK|BEGIN|$why"
  if [[ -n "$new_slug" ]]; then
    local stat;stat="$(app_state "$new_slug" 2>/dev/null || echo unavailable)"
    if [[ "$stat" != 'unavailable' ]]; then
      # A prior successful canary has boot:auto. Disable it BEFORE restarting 176.
      api POST "/addons/$new_slug/options" '{"boot":"manual","auto_update":false}' >/dev/null || return 1
    fi
    if [[ "$stat" == 'started' ]]; then api POST "/addons/$new_slug/stop" '{}' >/dev/null || true; fi
    stat="$(app_state "$new_slug" 2>/dev/null || echo unavailable)"
    if [[ "$stat" == 'started' ]]; then echo 'F136_CRITICAL|CANARY_COULD_NOT_STOP_DO_NOT_START_176';return 1;fi
  fi
  api POST "/addons/$old_slug/options" "$(jq -cn --arg boot "$old_boot" --argjson auto "$old_autoupdate" '{boot:$boot,auto_update:$auto}')" >/dev/null || return 1
  local oldstatus; oldstatus="$(app_state "$old_slug" 2>/dev/null || echo unavailable)"
  if [[ "$oldstatus" != 'started' ]]; then
    api POST "/addons/$old_slug/start" '{}' >/dev/null || return 1
  fi
  wait_state "$old_slug" started || return 1
  echo 'F136_ROLLBACK|PASS_176_RESTARTED_177_STOPPED'
  return 0
}
on_error(){
  local rc="$?"
  trap - ERR
  if [[ "$switched" == 1 && -n "$old_slug" ]]; then
    rollback_internal "SCRIPT_ERROR_$rc" || echo 'F136_CRITICAL|MANUAL_ROLLBACK_REQUIRED'
  fi
  exit "$rc"
}
trap on_error ERR
case "$MODE" in
  --status)
    original="$(original_slug)"; identity_guard "$original"
    current="$(app_state "$original")"
    candidate="$(app_state "$new_slug" 2>/dev/null || echo not-installed)"
    echo "F136_STATUS|176=$current|177=$candidate"
    exit 0;;
  --rollback)
    [[ -f "$STATE_PATH" ]] || fail 'NO_F136_STATE_FILE_FOR_ROLLBACK'
    old_slug="$(jq -r '.old_slug // ""' "$STATE_PATH")"
    new_slug="$(jq -r '.new_slug // ""' "$STATE_PATH")"
    old_boot="$(jq -r '.old_boot // "manual"' "$STATE_PATH")"
    old_autoupdate="$(jq -r '.old_autoupdate // false' "$STATE_PATH")"
    [[ "$new_slug" == "$CANARY_ID" && "$old_slug" =~ ^[0-9a-f]{8}_tng_iq_fanda_agent$ ]] || fail 'STATE_FILE_INVALID'
    identity_guard "$old_slug"
    rollback_internal 'USER_REQUESTED' || fail 'ROLLBACK_FAILED_REVIEW_SUPERVISOR'
    echo 'F136_FINAL|PASS_176_RESTORED_NEW_ADDON_KEPT_STOPPED'
    exit 0;;
esac
old_slug="$(original_slug)"
identity_guard "$old_slug"
old_info="$(app_info "$old_slug")" || fail 'ORIGINAL_ADDON_INFO_UNAVAILABLE'
old_state="$(jq -r '.data.state // ""' <<<"$old_info")"
old_boot="$(jq -r '.data.boot // "auto"' <<<"$old_info")"
old_autoupdate="$(jq -r '.data.auto_update // false' <<<"$old_info")"
old_options="$(jq -c '.data.options' <<<"$old_info")"
[[ "$old_options" != null && "$old_state" == 'started' ]] || fail 'ORIGINAL_176_NOT_RUNNING_OR_OPTIONS_MISSING'
[[ "$old_boot" == auto || "$old_boot" == manual ]] || fail 'INVALID_176_BOOT_POLICY'
[[ "$old_autoupdate" == true || "$old_autoupdate" == false ]] || fail 'INVALID_176_AUTO_UPDATE_POLICY'
[[ "$old_slug" != "$new_slug" ]] || fail 'ADDON_SLUG_COLLISION'
repo_config_check
[[ -d "$ROOT_CONFIGS/$old_slug" ]] || fail 'OLD_CONFIG_NOT_FOUND'
echo "F136_ORIGINAL|PASS_RUNNING_176_SLUG_$old_slug"
echo "F136_CANARY_SLUG|$new_slug"
if [[ "$MODE" == '--audit' ]]; then
  echo 'F136_FINAL|PASS_PRECHECK_ONLY_NO_CHANGES'
  exit 0
fi
[[ "$MODE" == '--apply' ]] || fail 'INVALID_MODE'
[[ ! -e "$STATE_PATH" ]] || fail 'PREVIOUS_MIGRATION_STATE_EXISTS_DO_NOT_REPEAT_APPLY'
# Full local Supervisor backup of this add-on PLUS its config folder, without HA history database.
backup_payload="$(jq -cn --arg old "$old_slug" --arg now "$STAMP" '{name:("F136 FANDA 00019 before Agent 0.1.177 " + $now),addons:[$old],homeassistant:false,folders:[],compressed:true,background:false}')"
backup="$(api POST /backups/new/partial "$backup_payload" 1500)" || fail 'PRE_CHANGE_APP_BACKUP_FAILED'
backup_slug="$(jq -r '.data.slug // ""' <<<"$backup")"
[[ "$backup_slug" =~ ^[0-9a-z]{6,32}$ ]] || fail 'BACKUP_SLUG_INVALID'
backup_info="$(api GET "/backups/$backup_slug/info")" || fail 'BACKUP_INFO_UNAVAILABLE'
jq -e --arg slug "$old_slug" '[(.data.addons // [])[]|.slug] | index($slug) != null' <<<"$backup_info" >/dev/null || fail 'BACKUP_DOES_NOT_INCLUDE_ORIGINAL_AGENT'
echo "F136_BACKUP|PASS_ORIGINAL_176|$backup_slug"
# Register canary repository; this action does NOT start a new runtime.
repos="$(api GET /store/repositories)" || fail 'REPOSITORY_READ_FAILED'
if ! jq -e --arg url "$CANARY_REPO" '(.data.repositories // .data // []) | any(.source==$url or .url==$url)' <<<"$repos" >/dev/null; then
  api POST /store/repositories "$(jq -cn --arg u "$CANARY_REPO" '{repository:$u}')" 180 >/dev/null || fail 'CANARY_REPOSITORY_REGISTRATION_FAILED'
fi
api POST /store/reload '{}' 180 >/dev/null || fail 'CANARY_STORE_RELOAD_FAILED'
store="$(api GET /store/addons)" || fail 'CANARY_STORE_UNAVAILABLE'
jq -e --arg slug "$new_slug" --arg ver "$VERSION_NEW" '(.data.addons // .data // []) | any(.slug==$slug and .version==$ver)' <<<"$store" >/dev/null || fail 'CANARY_NOT_IN_SUPERVISOR_STORE_EXPECTED_ID_AND_VERSION'
echo 'F136_STORE|PASS_CANARY_0177_VISIBLE_FOR_THIS_HOME_ASSISTANT'
# Install only while the package itself enforces boot: manual; fail if a runtime is started.
installed="$(api GET /addons)" || fail 'APP_LIST_AFTER_STORE_FAILED'
if ! jq -e --arg slug "$new_slug" '.data.addons | any(.slug==$slug)' <<<"$installed" >/dev/null; then
  api POST "/store/addons/$new_slug/install" '{"background":false}' 1500 >/dev/null || fail 'CANARY_INSTALL_FAILED_OLD_176_STILL_RUNNING'
fi
new_info="$(app_info "$new_slug")" || fail 'CANARY_INFO_AFTER_INSTALL_FAILED'
[[ "$(jq -r '.data.version // ""' <<<"$new_info")" == "$VERSION_NEW" ]] || fail 'CANARY_INSTALLED_VERSION_NOT_0177'
new_state="$(jq -r '.data.state // ""' <<<"$new_info")"
if [[ "$new_state" == 'started' ]]; then
  api POST "/addons/$new_slug/stop" '{}' >/dev/null || true
  fail 'CANARY_UNEXPECTEDLY_AUTOSTARTED_REVIEW_MANUALLY_NO_HANDOVER'
fi
[[ "$new_state" == 'stopped' ]] || fail 'CANARY_NOT_INSTALLED_IN_STOPPED_STATE'
api POST "/addons/$new_slug/options" '{"boot":"manual","auto_update":false}' >/dev/null || fail 'CANARY_SET_MANUAL_BOOT_FAILED'
[[ "$(app_state "$new_slug")" == stopped ]] || fail 'CANARY_RUNNING_BEFORE_ORIGINAL_STOP'
# Configuration is per app slug! Must copy original addon-config to new after stop.
[[ -d "$ROOT_CONFIGS/$new_slug" && -w "$ROOT_CONFIGS/$new_slug" ]] || fail 'NEW_CANARY_CONFIG_DIRECTORY_MISSING_OR_NOT_WRITABLE'
new_files="$(find "$ROOT_CONFIGS/$new_slug" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')"
[[ "$new_files" == 0 ]] || fail 'NEW_CANARY_CONFIG_NOT_EMPTY_AVOID_OVERWRITE'
old_size="$(du -sk "$ROOT_CONFIGS/$old_slug" | awk '{print $1}')"
[[ "$old_size" -lt 524288 ]] || fail 'ORIGINAL_CONFIG_TOO_LARGE_FOR_UNREVIEWED_COPY'
# Persist only non-secret pointers. No device tokens or private options go into share/logs.
state="$(jq -cn --arg old "$old_slug" --arg new "$new_slug" --arg boot "$old_boot" --argjson auto "$old_autoupdate" --arg backup "$backup_slug" --arg stamp "$STAMP" '{old_slug:$old,new_slug:$new,old_boot:$boot,old_autoupdate:$auto,backup_slug:$backup,created:$stamp}')"
(umask 077; printf '%s\n' "$state" > "$STATE_PATH")
# The first operation that can affect the running original Agent is here.
switched=1
api POST "/addons/$old_slug/options" '{"boot":"manual","auto_update":false}' >/dev/null || fail 'FAILED_TO_DISABLE_ORIGINAL_AUTOBOOT'
api POST "/addons/$old_slug/stop" '{}' 90 >/dev/null || fail 'FAILED_TO_STOP_176' 
wait_state "$old_slug" stopped || fail 'ORIGINAL_176_NOT_STOPPED'
[[ "$(app_state "$new_slug")" == stopped ]] || fail 'CANARY_STARTED_DURING_ORIGINAL_STOP'
cp -a "$ROOT_CONFIGS/$old_slug/." "$ROOT_CONFIGS/$new_slug/" || fail 'CANNOT_COPY_176_APP_CONFIG_TO_CANARY'
identity_guard "$new_slug"
old_sha="$(sha256sum "$ROOT_CONFIGS/$old_slug/device.json" | cut -d' ' -f1)"
new_sha="$(sha256sum "$ROOT_CONFIGS/$new_slug/device.json" | cut -d' ' -f1)"
[[ "$old_sha" == "$new_sha" ]] || fail 'MIGRATED_DEVICE_JSON_BYTES_NOT_IDENTICAL'
new_options="$(jq -cn --argjson old "$old_options" '{options:($old + {software_version:"0.1.177"}),boot:"manual",auto_update:false,ingress_panel:false}')"
api POST "/addons/$new_slug/options" "$new_options" >/dev/null || fail 'CANNOT_COPY_ORIGINAL_ADDON_OPTIONS'
[[ "$(app_state "$old_slug")" == stopped && "$(app_state "$new_slug")" == stopped ]] || fail 'TWO_AGENT_CONTROL_PRESTART_INVALID'
echo 'F136_HANDOVER|176_STOPPED|177_CONFIG_COPIED|TOKEN_BYTE_EXACT'
api POST "/addons/$new_slug/start" '{}' 120 >/dev/null || fail 'CANARY_START_FAILED'
wait_state "$new_slug" started || fail 'CANARY_NOT_RUNNING_AFTER_START'
[[ "$(app_state "$old_slug")" == stopped ]] || fail 'DUPLICATE_AGENTS_RUNNING_AFTER_CANARY_START'
# Boot auto only after one successful 00019 Agent heartbeat appears in its own logs.
healthy=0
for ((i=0;i<60;i++)); do
  logs="$(curl --silent --show-error --fail --connect-timeout 4 --max-time 20 -H "Authorization: Bearer $SUPERVISOR_TOKEN" "$SUPERVISOR_URL/addons/$new_slug/logs" 2>/dev/null || true)"
  if [[ "$logs" == *'Heartbeat'*'HTTP 200'* || "$logs" == *'Heartbeat úspěšný'* ]]; then healthy=1;break;fi
  [[ "$(app_state "$old_slug")" == stopped && "$(app_state "$new_slug")" == started ]] || fail 'POST_START_AGENT_STATES_CHANGED'
  sleep 2
done
[[ "$healthy" == 1 ]] || fail 'NO_CONFIRMED_NEW_AGENT_HEARTBEAT_ROLLBACK_TO_176'
api POST "/addons/$new_slug/options" '{"boot":"auto","auto_update":false}' >/dev/null || fail 'CANNOT_ENABLE_CANARY_BOOT_AFTER_HEARTBEAT'
[[ "$(app_state "$old_slug")" == stopped && "$(app_state "$new_slug")" == started ]] || fail 'POST_ENABLE_BOOT_DUPLICATE_AGENT'
switched=0
echo "F136_FINAL|PASS_00019_0177_ONLY_176_STOPPED|BACKUP=$backup_slug"
echo 'F136_GLOBAL_FLEET|UNCHANGED_0_1_176_MAIN'
echo 'F136_NEXT|VERIFY_00019_PORTAL_HEARTBEAT_0177_AND_THEN_EXPLICIT_RTU_READONLY'
echo "F136_ROLLBACK|bash F136_R1_00019_SUPERVISOR_CANARY_HANDOVER.sh --rollback"
