# Job Restart Feature

## Overview

The job restart feature allows users to retry failed or skipped entity mappings after a job has completed. This is useful when initial export attempts fail due to temporary issues like network problems, missing dependencies, or transient errors.

## How It Works

### Backend API

**Endpoint:** `POST /api/jobs/{job_id}/restart`

**Behavior:**
1. Validates that the job exists and is in a completed state (success or failed)
2. Checks that there are retryable entities (failed or skipped status)
3. Resets all failed/skipped entities to pending status
4. Clears error messages from those entities
5. Resets the job to running/exporting state
6. Triggers the export orchestrator in the background for this specific job

**Response:**
```json
{
  "status": "restart_initiated",
  "message": "Job 123 restarted: 45 entities reset to pending and export triggered",
  "reset_count": 45
}
```

**Error Cases:**
- 404: Job not found
- 400: Job is still running (cannot restart)
- 400: No failed/skipped entities to retry

### Frontend UI

The restart button appears in the expanded view of job cards when:
- The job status is `success` or `failed` (completed)
- There are entities with `failed` or `skipped` status

**User Experience:**
1. User expands a completed job card to view detailed status breakdown
2. If failed/skipped entities exist, a "Перезапустить неуспешные" button appears
3. User clicks the button
4. Button shows loading state ("Перезапуск…")
5. Toast notification confirms success or shows error
6. Job status updates automatically via existing polling mechanism
7. Progress bar reflects the new export progress

## Implementation Details

### Entity Status Flow

```
Initial:  pending → success/failed/skipped
Restart:  failed/skipped → pending → success/failed/skipped
```

Successful entities are never reset to maintain data consistency.

### Export Orchestrator Integration

The restart endpoint uses the same `orchestrate_mm_export(job_id)` function as the regular export workflow, but with job_id parameter to ensure only the specific job is processed. This maintains FIFO ordering and all existing export logic.

### Concurrency Handling

- The export orchestrator uses a global lock to prevent concurrent exports
- Jobs are processed in FIFO order based on created_at and id
- Restarted jobs enter the queue at their original position (based on creation time)

## Use Cases

1. **Network Failures:** Retry attachments that failed to download due to network issues
2. **Missing Dependencies:** After resolving dependency issues (e.g., missing users), retry failed mappings
3. **Transient Errors:** Retry entities that failed due to temporary Mattermost API issues
4. **Partial Success:** Complete jobs that were interrupted or had partial failures

## Testing

### Unit Tests
- `test_restart_job_not_found`: Validates 404 for missing jobs
- `test_restart_job_invalid_status`: Validates 400 for running jobs
- `test_restart_job_no_retryable_entities`: Validates 400 when no failed/skipped entities
- `test_restart_job_success`: Validates successful restart flow

### Integration Tests
- `test_job_restart_integration`: End-to-end validation with database
- `test_restart_running_job_fails`: Validates running job rejection
- `test_restart_job_without_retryable_entities`: Validates all-success job rejection

## Security Considerations

- Job ID validation prevents unauthorized access to other jobs
- Status checks prevent restarting jobs in inconsistent states
- Background task execution prevents blocking the API
- SQL queries use parameterized statements to prevent injection

## Future Enhancements

Potential improvements for future iterations:
- Selective restart (choose specific entity types to retry)
- Retry limits to prevent infinite loops
- Retry history tracking
- Bulk restart for multiple jobs
- Retry with modified configuration (e.g., different timeouts)
