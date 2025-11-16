# Changelog

All notable changes to the MM Importer plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2025-11-16

### Added
- **Mark-as-read functionality**: Imported posts are now automatically marked as read for all channel members
  - Updates `LastViewedAt` timestamp for all channel members after post creation
  - Resets mention counts to 0
  - Prevents false notifications during bulk import operations
  - Uses direct database access via Mattermost's Driver interface
  - Graceful error handling - failures don't block post import
- Comprehensive documentation for mark-as-read implementation (`MARK_AS_READ.md`)
- Test script for validating mark-as-read functionality (`test-mark-as-read.sh`)
- Unit tests for helper functions

### Changed
- Updated plugin README with mark-as-read feature description and testing instructions

### Technical Details
- Implementation follows Mattermost's recommended approach for bulk import operations
- Compatible with both PostgreSQL and MySQL databases
- Handles pagination for channels with many members
- Only updates timestamps if newer than current `LastViewedAt` to prevent marking newer posts as unread

## [0.3.1] - Previous Release

(Previous changelog entries would go here)
