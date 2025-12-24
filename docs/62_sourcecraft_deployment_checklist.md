# SourceCraft Deployment Checklist

This checklist ensures all necessary steps are completed when deploying the Paperbird application to SourceCraft.

## Pre-Deployment

### Code Preparation
- [ ] Ensure all code is committed and pushed to the repository
- [ ] Verify that `requirements.txt` is up to date
- [ ] Check that all necessary documentation is included
- [ ] Verify that `.gitignore` is properly configured
- [ ] Ensure sensitive files are not committed (`.env`, secrets, etc.)

### Environment Assessment
- [ ] Identify all required environment variables
- [ ] Document all API keys and secrets needed
- [ ] List all external services the application depends on
- [ ] Identify any custom domain requirements
- [ ] Document SSL certificate requirements

## SourceCraft Repository Setup

### Repository Creation
- [ ] Create new repository on SourceCraft
- [ ] Set appropriate repository name and description
- [ ] Configure repository visibility (public/private)
- [ ] Add necessary team members with appropriate permissions
- [ ] Set up branch protection rules if needed

### Code Push
- [ ] Clone the SourceCraft repository locally
- [ ] Push existing code to SourceCraft repository
- [ ] Verify all files are correctly uploaded
- [ ] Check that file permissions are preserved
- [ ] Verify that large files are handled appropriately

## Environment Configuration

### Environment Variables
- [ ] Create environment variables in SourceCraft:
  - [ ] `DJANGO_SECRET_KEY`
  - [ ] `DJANGO_DEBUG` (set to `False`)
  - [ ] `DJANGO_ALLOWED_HOSTS`
  - [ ] `POSTGRES_DB`
  - [ ] `POSTGRES_USER`
  - [ ] `POSTGRES_PASSWORD`
  - [ ] `POSTGRES_HOST`
  - [ ] `POSTGRES_PORT`
  - [ ] `TELEGRAM_API_ID`
  - [ ] `TELEGRAM_API_HASH`
  - [ ] `TELEGRAM_SESSION`
  - [ ] `OPENAI_API_KEY`
  - [ ] `OPENAI_MODEL`
  - [ ] `OPENAI_TIMEOUT`
  - [ ] `OPENAI_IMAGE_URL`
  - [ ] `OPENAI_IMAGE_MODEL`
  - [ ] `OPENAI_IMAGE_SIZE`
  - [ ] `OPENAI_IMAGE_QUALITY`
  - [ ] `OPENAI_IMAGE_RESPONSE_FORMAT`
  - [ ] `OPENAI_IMAGE_TIMEOUT`
  - [ ] `YANDEX_API_KEY`
  - [ ] `YANDEX_FOLDER_ID`
  - [ ] `YANDEX_TIMEOUT`
  - [ ] `YANDEX_IMAGE_MODEL`
  - [ ] `YANDEX_IMAGE_SIZE`
  - [ ] `YANDEX_IMAGE_QUALITY`
  - [ ] `YANDEX_IMAGE_POLL_INTERVAL`
  - [ ] `YANDEX_IMAGE_POLL_TIMEOUT`
  - [ ] `YANDEX_IMAGE_TIMEOUT`
  - [ ] `GEMINI_API_KEY`
  - [ ] `GEMINI_MODEL`
  - [ ] `GEMINI_TIMEOUT`
- [ ] Verify all environment variables are correctly set
- [ ] Ensure sensitive variables are properly secured
- [ ] Document any environment-specific configurations

### Secrets Management
- [ ] Identify all secrets that need secure storage
- [ ] Configure secret storage in SourceCraft
- [ ] Verify that secrets are not exposed in logs
- [ ] Set up rotation procedures for secrets

## Database Configuration

### Database Setup
- [ ] Create PostgreSQL database instance
- [ ] Configure database user and permissions
- [ ] Set up database connection parameters
- [ ] Verify database connectivity from application
- [ ] Configure database backup procedures

### Initial Database Setup
- [ ] Run initial database migrations:
  ```bash
  python manage.py migrate
  ```
- [ ] Create superuser account:
  ```bash
  python manage.py createsuperuser
  ```
- [ ] Verify database schema is correctly applied
- [ ] Test database connectivity and performance

## Application Configuration

### Static Files
- [ ] Configure static file storage
- [ ] Run static file collection:
  ```bash
  python manage.py collectstatic --noinput
  ```
- [ ] Verify static files are correctly served
- [ ] Test CSS and JavaScript assets

### Media Files
- [ ] Configure media file storage
- [ ] Set up appropriate permissions for media uploads
- [ ] Verify media file handling
- [ ] Test file upload functionality

## Worker Processes

### Worker Configuration
- [ ] Configure collector worker:
  ```bash
  python manage.py run_worker collector
  ```
- [ ] Configure web collector worker:
  ```bash
  python manage.py run_worker collector_web
  ```
- [ ] Configure rewrite worker:
  ```bash
  python manage.py run_worker rewrite
  ```
- [ ] Configure publish worker:
  ```bash
  python manage.py run_worker publish
  ```
- [ ] Configure image worker:
  ```bash
  python manage.py run_worker image
  ```
- [ ] Configure maintenance worker:
  ```bash
  python manage.py run_worker maintenance
  ```
- [ ] Configure source worker:
  ```bash
  python manage.py run_worker source
  ```

### Worker Monitoring
- [ ] Set up process monitoring for all workers
- [ ] Configure restart policies for worker processes
- [ ] Set up logging for worker processes
- [ ] Configure alerting for worker failures

## Web Server Configuration

### WSGI Setup
- [ ] Configure WSGI server (Gunicorn, uWSGI, etc.)
- [ ] Set up appropriate number of worker processes
- [ ] Configure request timeout settings
- [ ] Set up request logging

### Load Balancer (if applicable)
- [ ] Configure load balancer settings
- [ ] Set up health checks
- [ ] Configure SSL termination
- [ ] Set up caching if appropriate

## Domain and SSL Configuration

### Domain Setup
- [ ] Configure custom domain in SourceCraft
- [ ] Set up DNS records
- [ ] Verify domain resolution
- [ ] Configure domain aliases if needed

### SSL Certificate
- [ ] Obtain SSL certificate
- [ ] Configure SSL certificate in SourceCraft
- [ ] Verify SSL certificate installation
- [ ] Test HTTPS connectivity

## Testing

### Functional Testing
- [ ] Test basic application functionality
- [ ] Verify user authentication works
- [ ] Test content collection from Telegram
- [ ] Verify content rewriting functionality
- [ ] Test content publishing to Telegram
- [ ] Verify image generation works
- [ ] Test all worker processes

### Performance Testing
- [ ] Test application response times
- [ ] Verify database query performance
- [ ] Test static file serving performance
- [ ] Verify worker process performance

### Security Testing
- [ ] Verify environment variables are secure
- [ ] Test authentication mechanisms
- [ ] Verify API key security
- [ ] Check for common security vulnerabilities

## Monitoring and Logging

### Application Monitoring
- [ ] Set up application performance monitoring
- [ ] Configure error tracking
- [ ] Set up uptime monitoring
- [ ] Configure resource usage monitoring

### Log Management
- [ ] Configure log aggregation
- [ ] Set up log retention policies
- [ ] Configure log alerting
- [ ] Verify log accessibility

## Post-Deployment

### Verification
- [ ] Verify application is accessible
- [ ] Test all core functionality
- [ ] Verify all worker processes are running
- [ ] Check database connectivity
- [ ] Verify static and media files are served correctly

### Documentation
- [ ] Update deployment documentation
- [ ] Document any deployment-specific configurations
- [ ] Record any issues encountered and their solutions
- [ ] Update runbook with deployment procedures

### Communication
- [ ] Notify stakeholders of deployment completion
- [ ] Provide access information to relevant teams
- [ ] Document any post-deployment tasks
- [ ] Schedule post-deployment review

## Maintenance Procedures

### Regular Maintenance
- [ ] Schedule regular database backups
- [ ] Plan for dependency updates
- [ ] Schedule security audits
- [ ] Plan for performance reviews

### Emergency Procedures
- [ ] Document rollback procedures
- [ ] Set up incident response procedures
- [ ] Document contact information for critical issues
- [ ] Plan for disaster recovery

## Scaling Considerations

### Horizontal Scaling
- [ ] Plan for adding additional web servers
- [ ] Plan for scaling worker processes
- [ ] Consider database read replicas
- [ ] Plan for CDN integration

### Vertical Scaling
- [ ] Monitor resource usage
- [ ] Plan for server upgrades
- [ ] Consider database scaling options
- [ ] Plan for storage expansion
