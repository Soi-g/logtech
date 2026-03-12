# Bugfix Requirements Document

## Introduction

This document addresses four critical bugs in the AWS observability platform that prevent proper monitoring and alerting functionality:

1. **SNS Subject None Error**: Lambda crashes when processing SNS messages with null subject
2. **Log and Trace Collection Failures**: metrics_agent, logs_agent, traces_agent not collecting data from OpenSearch
3. **OSIS EOF Error**: EC2 instances cannot send logs/traces to OSIS ingestion endpoints due to security group misconfiguration
4. **Runbook Search Failure**: Knowledge Base returns 404 error when attempting to retrieve runbooks

These bugs collectively prevent the observability platform from functioning, impacting incident detection, analysis, and resolution capabilities.

## Bug Analysis

### Current Behavior (Defect)

#### Bug 1: SNS Subject None Error

1.1 WHEN an SNS message arrives with `"Subject": null` in the payload THEN the Lambda function crashes with `TypeError: argument of type 'NoneType' is not iterable` at line 649 in `bedrock_agent_runtime_handler.py`

1.2 WHEN the code executes `if '[FIRING' in subject or '[RESOLVED' in subject:` with subject=None THEN Python raises a TypeError because the `in` operator cannot iterate over NoneType

#### Bug 2: Log and Trace Collection Failures

1.3 WHEN metrics_agent, logs_agent, or traces_agent are invoked to collect observability data THEN they return "데이터 없음" (no data) despite data existing in OpenSearch

1.4 WHEN the SNS message format is `{"AlarmName":"HighHttpErrorRate","NewStateValue":"ALARM","NewStateReason":"HTTP error rate exceeded 5%"}` without a Subject field THEN the alert processing may fail to extract alert metadata correctly

#### Bug 3: OSIS EOF Error

1.5 WHEN EC2 instances attempt to send logs or traces to OSIS ingestion endpoints on port 443 THEN the connection fails with EOF error

1.6 WHEN OSIS pipelines are configured with `security_group_ids = [aws_security_group.opensearch.id]` THEN there is no explicit inbound rule allowing EC2 instances to connect to OSIS endpoints on port 443

#### Bug 4: Runbook Search Failure

1.7 WHEN the Lambda function calls `search_runbooks()` to retrieve runbooks from the Knowledge Base THEN it returns `An error occurred (404) when calling the Retrieve operation: UnknownOperationException`

1.8 WHEN the Knowledge Base ID OTYVFZOAJE is used in the Retrieve API call THEN the Bedrock service returns a 404 error indicating the resource or operation is not found

### Expected Behavior (Correct)

#### Bug 1: SNS Subject None Error

2.1 WHEN an SNS message arrives with `"Subject": null` THEN the Lambda function SHALL safely handle the None value by converting it to an empty string before string operations

2.2 WHEN subject is None THEN the code SHALL execute `subject = ''` before any string comparison operations to prevent TypeError

#### Bug 2: Log and Trace Collection Failures

2.3 WHEN metrics_agent, logs_agent, or traces_agent are invoked THEN they SHALL successfully query OpenSearch and return available observability data

2.4 WHEN SNS messages arrive in various formats (with or without Subject field) THEN the alert processing SHALL correctly extract alert metadata and trigger appropriate data collection

#### Bug 3: OSIS EOF Error

2.5 WHEN EC2 instances attempt to send logs or traces to OSIS ingestion endpoints THEN the connection SHALL succeed and data SHALL be ingested into OpenSearch

2.6 WHEN OSIS pipelines are deployed THEN security group rules SHALL explicitly allow inbound traffic from EC2 security groups to OSIS endpoints on port 443

#### Bug 4: Runbook Search Failure

2.7 WHEN the Lambda function calls `search_runbooks()` THEN it SHALL successfully retrieve runbooks from the Knowledge Base without 404 errors

2.8 WHEN the Knowledge Base is configured and indexed THEN the Bedrock Retrieve API SHALL return relevant runbook documents for the given alert name

### Unchanged Behavior (Regression Prevention)

#### Bug 1: SNS Subject None Error

3.1 WHEN an SNS message arrives with a valid Subject field containing "[FIRING" or "[RESOLVED" THEN the system SHALL CONTINUE TO correctly parse the alert name and state

3.2 WHEN the Lambda function processes Alertmanager-formatted SNS messages THEN it SHALL CONTINUE TO extract severity, alert name, and state information correctly

#### Bug 2: Log and Trace Collection Failures

3.3 WHEN observability data exists in AMP and OpenSearch THEN the collection agents SHALL CONTINUE TO query and return metrics, logs, and traces as designed

3.4 WHEN the Lambda function invokes the Bedrock agent with collected data THEN it SHALL CONTINUE TO generate analysis and send results to Slack

#### Bug 3: OSIS EOF Error

3.5 WHEN OSIS pipelines receive data from EC2 instances THEN they SHALL CONTINUE TO process and forward logs/traces to OpenSearch domain correctly

3.6 WHEN OpenSearch security group rules allow Lambda access THEN Lambda functions SHALL CONTINUE TO query OpenSearch without connectivity issues

#### Bug 4: Runbook Search Failure

3.7 WHEN runbooks are uploaded to S3 bucket `s3://log-platform-dev-runbooks-024249678948/runbooks/` THEN the Knowledge Base data source SHALL CONTINUE TO sync and index them

3.8 WHEN the Knowledge Base ingestion job completes successfully THEN the indexed documents SHALL CONTINUE TO be available for retrieval
