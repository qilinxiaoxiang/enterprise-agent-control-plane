#!/usr/bin/env bash
set -euo pipefail

: "${CONTROL_URL:?Set CONTROL_URL to the Cloud Run control service URL}"
: "${OPERATOR_TOKEN:?Set OPERATOR_TOKEN to a Firebase operator JWT}"
: "${APPROVER_TOKEN:?Set APPROVER_TOKEN to a Firebase approver JWT}"

curl --fail --silent --show-error "${CONTROL_URL}/v1/health" | jq -e '.status == "ok"' >/dev/null
curl --fail --silent --show-error "${CONTROL_URL}/v1/runs" | jq -e 'length >= 3' >/dev/null

anonymous_status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --request POST "${CONTROL_URL}/v1/cases" \
  --header 'Content-Type: application/json' \
  --data '{}')
test "${anonymous_status}" = "401"

suffix=$(date +%s)
case_response=$(curl --fail --silent --show-error \
  --request POST "${CONTROL_URL}/v1/cases" \
  --header "Authorization: Bearer ${OPERATOR_TOKEN}" \
  --header 'Content-Type: application/json' \
  --data "{\"tenant_id\":\"northstar-bank\",\"customer_id\":\"cust-cloud-${suffix}\",\"transfer_id\":\"txn-cloud-${suffix}\",\"anomaly_type\":\"profile_mismatch\",\"requested_action\":\"release_transfer\",\"amount\":25000,\"currency\":\"USD\",\"notes\":\"Cloud deployment smoke test\"}")
case_id=$(jq -r '.case_id' <<<"${case_response}")

run_response=$(curl --fail --silent --show-error \
  --request POST "${CONTROL_URL}/v1/cases/${case_id}/runs" \
  --header "Authorization: Bearer ${OPERATOR_TOKEN}")
run_id=$(jq -r '.run_id' <<<"${run_response}")
action_hash=$(jq -r '.state.__interrupt__[0].proposed_action_hash' <<<"${run_response}")
test "$(jq -r '.status' <<<"${run_response}")" = "awaiting_approval"

final_response=$(curl --fail --silent --show-error \
  --request POST "${CONTROL_URL}/v1/runs/${run_id}/approvals" \
  --header "Authorization: Bearer ${APPROVER_TOKEN}" \
  --header 'Content-Type: application/json' \
  --data "{\"approved\":true,\"comment\":\"Cloud smoke verified\",\"proposed_action_hash\":\"${action_hash}\"}")
jq -e '.status == "completed" and .state.outcome_verified == true' <<<"${final_response}" >/dev/null
printf 'cloud-smoke passed: %s/v1/runs/%s\n' "${CONTROL_URL}" "${run_id}"
