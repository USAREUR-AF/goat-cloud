"""
State Manager — DynamoDB CRUD for Hunterkiller Notification Tracking

DynamoDB table schema:
  - PK: resource_arn (String)
  - Attributes:
    - batch_id: which scan batch this belongs to
    - status: warned | enforcing | torn_down | resolved | protected | error | dry_run | aborted
    - warned_at: ISO timestamp when warning was sent
    - grace_deadline: ISO timestamp when enforcement can begin
    - contact_email: who was notified
    - contact_source: how contact was resolved (tag:X, iam:user, admin_fallback)
    - resource_type, resource_name, region, estimated_monthly_cost
    - issues: list of compliance issues
    - teardown_result: output from execution (if executed)
    - scan_date: which compliance scan triggered this
    - ttl_expires: DynamoDB TTL (epoch seconds)
    - approval_status: pending | approved | expired (admin approval gate for executor)
    - approved_by: email of admin who approved teardown
    - approved_at: ISO timestamp when approval was granted
"""

from datetime import UTC, datetime, timedelta

import boto3
from boto3.dynamodb.conditions import Attr


class StateManager:
    def __init__(self, table_name, ttl_days=30):
        self.table_name = table_name
        self.ttl_days = ttl_days
        self._table = None

    @property
    def table(self):
        if self._table is None:
            dynamodb = boto3.resource("dynamodb")
            self._table = dynamodb.Table(self.table_name)
        return self._table

    def get_record(self, resource_arn):
        """Get a single record by ARN. Returns dict or None."""
        try:
            response = self.table.get_item(Key={"resource_arn": resource_arn})
            return response.get("Item")
        except Exception as e:
            print(f"  DynamoDB get error for {resource_arn}: {e}")
            return None

    def write_warning(
        self,
        resource_arn,
        batch_id,
        scan_date,
        contact_email,
        contact_source,
        resource_type,
        resource_name,
        region,
        estimated_monthly_cost,
        issues,
        grace_period_hours,
        tags=None,
    ):
        """Write a new warning record."""
        now = datetime.now(UTC)
        grace_deadline = now + timedelta(hours=grace_period_hours)
        ttl_expires = int((now + timedelta(days=self.ttl_days)).timestamp())

        item = {
            "resource_arn": resource_arn,
            "batch_id": batch_id,
            "status": "warned",
            "warned_at": now.isoformat(),
            "grace_deadline": grace_deadline.isoformat(),
            "contact_email": contact_email,
            "contact_source": contact_source,
            "resource_type": resource_type,
            "resource_name": resource_name,
            "region": region,
            "estimated_monthly_cost": str(estimated_monthly_cost or 0),
            "issues": issues,
            "scan_date": scan_date,
            "ttl_expires": ttl_expires,
            "tags": tags if tags else {},
            "approval_status": "pending",
            "approved_by": "",
            "approved_at": "",
        }

        try:
            self.table.put_item(Item=item)
            print(f"  Wrote warning: {resource_arn} → {contact_email} ({contact_source})")
        except Exception as e:
            print(f"  DynamoDB write error for {resource_arn}: {e}")

    def get_warned_records(self, batch_id, all_batches=False):
        """Get the warned records of one batch, or of every batch when all_batches is set."""
        if all_batches:
            condition = Attr("status").eq("warned")
        else:
            condition = Attr("batch_id").eq(batch_id) & Attr("status").eq("warned")
        try:
            response = self.table.scan(FilterExpression=condition)
            records = response.get("Items", [])

            # Handle pagination
            while "LastEvaluatedKey" in response:
                response = self.table.scan(
                    FilterExpression=condition,
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                records.extend(response.get("Items", []))

            return records
        except Exception as e:
            print(f"  DynamoDB scan error for batch {batch_id}: {e}")
            return []

    def update_status(self, resource_arn, status, teardown_result=""):
        """Update the status of a record."""
        try:
            update_expr = "SET #s = :status, updated_at = :now"
            expr_values = {
                ":status": status,
                ":now": datetime.now(UTC).isoformat(),
            }
            expr_names = {"#s": "status"}

            if teardown_result:
                update_expr += ", teardown_result = :result"
                expr_values[":result"] = teardown_result

            self.table.update_item(
                Key={"resource_arn": resource_arn},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expr_values,
                ExpressionAttributeNames=expr_names,
            )
        except Exception as e:
            print(f"  DynamoDB update error for {resource_arn}: {e}")

    def reconcile_stale_warnings(self, current_finding_arns):
        """Auto-resolve warned resources that are no longer in the current findings.

        When a new scan produces no finding for a previously warned resource
        (e.g., the resource was fixed or the scanner logic changed), the stale
        warning is set to 'resolved' so it no longer appears as pending.
        """
        try:
            response = self.table.scan(FilterExpression=Attr("status").eq("warned"))
            records = response.get("Items", [])
            while "LastEvaluatedKey" in response:
                response = self.table.scan(
                    FilterExpression=Attr("status").eq("warned"),
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )
                records.extend(response.get("Items", []))
        except Exception as e:
            print(f"  Reconcile scan error: {e}")
            return 0

        resolved = 0
        for record in records:
            arn = record["resource_arn"]
            if arn not in current_finding_arns:
                self.update_status(arn, "resolved", teardown_result="auto-resolved: no longer in findings")
                print(f"  Resolved stale warning: {arn}")
                resolved += 1

        return resolved

    def update_approval_status(self, resource_arn, approval_status, approved_by=""):
        """Update the approval status of a record (pending → approved → expired)."""
        try:
            now = datetime.now(UTC).isoformat()
            update_expr = "SET approval_status = :approval_status, updated_at = :now"
            expr_values = {
                ":approval_status": approval_status,
                ":now": now,
            }

            if approved_by:
                update_expr += ", approved_by = :approved_by, approved_at = :approved_at"
                expr_values[":approved_by"] = approved_by
                expr_values[":approved_at"] = now

            self.table.update_item(
                Key={"resource_arn": resource_arn},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expr_values,
            )
            print(
                f"  Approval updated: {resource_arn} → {approval_status}{' by ' + approved_by if approved_by else ''}"
            )
        except Exception as e:
            print(f"  DynamoDB approval update error for {resource_arn}: {e}")
