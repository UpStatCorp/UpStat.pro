# UpStat: An AI-Powered Sales Call Analysis and Manager Coaching Platform

**Diploma Project Report**

---

**Project Title:** UpStat — A Web-Based Intelligent Sales Analytics and Coaching System

**Technology Stack:** Python, FastAPI, SQLAlchemy, OpenAI GPT-4o, ElevenLabs, Azure Voice Live, Docker

**Academic Year:** 2025–2026

---

## Table of Contents

1. [Abstract](#1-abstract)
2. [Introduction](#2-introduction)
   - 2.1 [Problem Background](#21-problem-background)
   - 2.2 [Aims and Objectives](#22-aims-and-objectives)
3. [Literature Review](#3-literature-review)
   - 3.1 [Sales Performance Management and CRM Platforms](#31-sales-performance-management-and-crm-platforms)
   - 3.2 [Artificial Intelligence in Sales Coaching](#32-artificial-intelligence-in-sales-coaching)
   - 3.3 [Voice AI and Conversational Analysis](#33-voice-ai-and-conversational-analysis)
   - 3.4 [Web Application Architecture for SaaS Platforms](#34-web-application-architecture-for-saas-platforms)
4. [Design and Methodology](#4-design-and-methodology)
   - 4.1 [Development Methodology](#41-development-methodology)
   - 4.2 [System Architecture](#42-system-architecture)
   - 4.3 [Database Design](#43-database-design)
   - 4.4 [Authentication and Authorization Design](#44-authentication-and-authorization-design)
   - 4.5 [API Design](#45-api-design)
   - 4.6 [AI Subsystem Design](#46-ai-subsystem-design)
5. [Implementation](#5-implementation)
   - 5.1 [Backend Implementation](#51-backend-implementation)
   - 5.2 [Frontend Implementation](#52-frontend-implementation)
   - 5.3 [Deployment and DevOps](#53-deployment-and-devops)
   - 5.4 [Additional AI Services](#54-additional-ai-services)
6. [Results and Discussion](#6-results-and-discussion)
   - 6.1 [Achieved Results](#61-achieved-results)
   - 6.2 [Limitations and Known Issues](#62-limitations-and-known-issues)
   - 6.3 [Discussion and Future Work](#63-discussion-and-future-work)
7. [Conclusion](#7-conclusion)
8. [References](#8-references)
9. [Appendix](#9-appendix)

---

## 1. Abstract

This report documents the design, implementation, and evaluation of **UpStat**, a web-based platform for intelligent sales call analysis and sales manager coaching. The system was developed as a full-stack software project to address a widely observed problem in business-to-business sales organisations: the absence of structured, data-driven feedback on individual sales conversations. Most companies rely on subjective manager impressions or infrequent manual review sessions, leaving systematic quality issues undetected until they have already caused revenue loss.

UpStat automates the process of uploading sales call recordings, transcribing them, scoring them against configurable evaluation checklists, and generating structured coaching recommendations for sales managers. The platform further enables team owners and sales directors to monitor aggregate performance trends through a multi-mode Owner Command Center dashboard that categorises revenue leakage, conversion risk, and deal velocity metrics. Artificial intelligence is central to the product: OpenAI GPT-4o drives the primary call analysis and report generation pipeline, GPT-4o-mini handles lighter inference tasks such as training plan extraction and action identification, ElevenLabs Scribe v1 provides speaker-diarised speech-to-text transcription, and Azure Voice Live enables real-time bidirectional voice coaching sessions.

The backend is built on Python 3 with FastAPI, persisting data through SQLAlchemy 2 to either SQLite (development) or PostgreSQL (production). The frontend is rendered server-side using Jinja2 templates with minimal JavaScript for interactive components. The system is containerised with Docker and served behind Nginx in production. Additional integrations include Bitrix24-compatible CRM webhook ingestion, SMTP email reporting, and Google OAuth.

The resulting prototype successfully implements the core value proposition: a sales team using UpStat can identify the specific behavioural patterns that cause revenue loss, assign targeted voice-based practice sessions to individual managers, and track improvement over time through a quantitative seller passport. Known limitations include the absence of a production-grade message queue for asynchronous pipeline processing, partial UI mobile responsiveness, and the fact that certain AI scoring parameters are still approximations rather than validated models. Future work is outlined in Section 6.3.

---

## 2. Introduction

### 2.1 Problem Background

Sales teams in small and medium-sized enterprises across post-Soviet and broader emerging markets face a structural challenge that has resisted easy solution: the gap between the number of customer conversations that happen daily and the capacity of sales leadership to evaluate and improve those conversations in any meaningful way. A typical sales department of five to twenty managers may collectively handle dozens or even hundreds of inbound and outbound calls per day. A sales director or head of sales simply cannot listen to more than a small fraction of these recordings, and even when they do, feedback is typically delivered verbally, rarely tracked, and inconsistently applied.

The consequences of this gap compound over time. Managers develop and reinforce poor habits — failing to confirm a next step at the end of a conversation, allowing price discussions before establishing value, or misidentifying decision makers — with no systematic corrective feedback. The team's overall conversion rate reflects the average of individual behaviours, and those behaviours are shaped more by accidental learning and peer imitation than by deliberate coaching. Revenue is lost not because the product is weak or the market is unfavourable, but because specific, correctable behavioural patterns in customer conversations are never identified and addressed.

Existing solutions partially address this problem. CRM systems such as Bitrix24, AmoCRM, and Salesforce record deal stages and outcomes but do not analyse the quality of the conversations that lead to those outcomes. Call recording platforms store audio files but require manual listening. Enterprise sales coaching tools such as Gong.io and Chorus.ai provide sophisticated AI-driven conversation analytics but are priced for large enterprise customers and are not localised or adapted to the conversational and business norms of markets in Russia, Kazakhstan, and neighbouring countries. Smaller organisations cannot afford these tools and often have no viable alternative.

UpStat was conceived as a practical, deployable answer to this gap. It provides automated analysis of recorded sales calls, structured scoring against a configurable evaluation framework, targeted one-error-at-a-time coaching via real-time voice practice sessions, and a top-level owner dashboard that translates behavioural patterns into estimated revenue impact. The system is designed to be understandable and actionable by sales owners and managers who are not data scientists — the outputs use plain language and monetary estimates rather than abstract scores.

### 2.2 Aims and Objectives

**Main Aim:** To design and implement a web-based platform that automates the analysis of sales call recordings using artificial intelligence, generates personalised coaching plans for individual managers, and provides team owners with a revenue-centric performance dashboard.

**Specific Objectives:**

**O1.** Design and implement a multi-stage call analysis pipeline that accepts audio file uploads, performs speaker-diarised speech-to-text transcription via ElevenLabs Scribe v1, evaluates the resulting transcript against configurable JSON checklists, and generates a structured coaching report using OpenAI GPT-4o.

**O2.** Build a FastAPI-based RESTful backend with modular routers covering authentication, call management, team administration, CRM integration, analytics, voice training, and owner reporting, with SQLAlchemy 2 as the persistence layer.

**O3.** Implement a role-based access control system with at least three distinct roles — `manager`, `owner`, and `admin` — using cookie-based session authentication, bcrypt password hashing, and Google OAuth as an alternative sign-in method.

**O4.** Develop a server-side rendered frontend using Jinja2 templates and vanilla JavaScript, covering all primary user workflows: call upload, transcript review, training plan view, voice training session, team analytics, CRM recordings, and owner dashboard.

**O5.** Design and implement a Seller Passport feature that aggregates a manager's scored performance across five sales stages into a persistent profile, tracks deltas between coaching cycles, and provides owners with a comparative view of team members.

**O6.** Build a Manager Actions Collection system that uses GPT-4o-mini to extract specific verbal actions from each analysed call, accumulates these across the team, detects recurring positive and negative patterns when a threshold is reached, and automatically emails a pattern summary report to the team owner.

**O7.** Create a real-time voice coaching module that connects a manager's browser microphone to the Azure Voice Live API over WebSocket, injects a training-specific system prompt based on the manager's most recent coaching recommendation, and records the full session transcript to the database.

**O8.** Implement an Owner Command Center dashboard with six switchable analysis modes — Money, Conversion, Risk, Speed, Team, and Forecast — aggregating parameter values and CRM deal data across the team, and surfacing estimated revenue leakage with natural-language explanations.

**O9.** Integrate with Bitrix24-compatible CRM systems to automatically ingest call recordings, synchronise deal and contact entities, and map CRM manager names to platform user accounts for attribution of analyses.

**O10.** Containerise the application using Docker and Docker Compose, configure Nginx as a reverse proxy, and document the deployment process to allow the system to be stood up on a standard cloud virtual machine.

---

## 3. Literature Review

### 3.1 Sales Performance Management and CRM Platforms

Customer Relationship Management systems have served as the primary data repository for sales operations since the early 2000s. Salesforce, introduced in 1999, established the model of cloud-hosted pipeline tracking that was subsequently adopted across the market (Greenberg, 2010). Later platforms such as HubSpot, Pipedrive, Bitrix24, and AmoCRM offered the same core concept — deal stages, contact records, activity logging — at lower price points and with greater localisation for non-English-speaking markets.

However, a fundamental limitation of CRM-centric approaches to sales management is that they record outcomes rather than behaviours. A deal marked "lost" in Bitrix24 carries no information about what was said during the calls that preceded that outcome. Sales managers and coaches who want to improve conversion rates must bridge the gap between CRM outcome data and the actual conversational content of customer interactions. This gap is not addressed by standard CRM tooling.

Some platforms have attempted to close this gap through add-on integrations. Bitrix24, for instance, offers a basic call recording feature and a telephony integration API, but provides no analytical capability beyond storage. AmoCRM similarly stores call recordings as attachments to contacts but offers no evaluation logic. The analytical depth required to systematically improve sales behaviour falls outside the scope of what these platforms were designed to provide.

Enterprise-grade conversation analytics platforms — notably Gong.io and Chorus.ai (now part of ZoomInfo) — represent the state of the art in automated sales call analysis (Gong.io, 2023; ZoomInfo, 2023). These products use machine learning to identify talk-to-listen ratios, keyword mentions, competitor discussions, and deal risk signals from call recordings. Gong.io in particular has published research correlating specific conversational behaviours with win rates, establishing a basis for evidence-driven sales coaching.

The limitations of these platforms for UpStat's target market are primarily commercial and contextual. Enterprise conversation analytics tools are priced at hundreds of dollars per user per month, making them inaccessible to teams with five to twenty managers. They are designed for English-language sales conversations and North American business contexts. They do not offer configurable evaluation frameworks that can be adapted to local sales methodologies or custom product categories. UpStat addresses these gaps by providing a self-hosted, configurable, and locally deployable alternative.

### 3.2 Artificial Intelligence in Sales Coaching

The application of large language models to sales coaching is a relatively recent development enabled by the commercial availability of capable foundation models from 2022 onwards. Prior to this period, sales coaching technology relied primarily on keyword spotting, sentiment analysis using classical NLP techniques, and rule-based classification of conversational phases (Rapp et al., 2017).

The release of GPT-3 and subsequently GPT-4 by OpenAI created new possibilities for qualitative evaluation of open-ended text. Rather than detecting the presence or absence of specific words, it became possible to instruct a language model to evaluate whether a manager genuinely explored the client's situation, whether the value proposition was linked to the client's stated needs, or whether objections were handled substantively rather than dismissed. This qualitative dimension of evaluation was previously only achievable through human review.

Several startups have explored this space since 2022. Tools such as Sybill.ai, Wonderway, and Second Nature offer various combinations of call analysis, roleplay practice, and coaching recommendation generation (Sybill.ai, 2023; Wonderway, 2023). Second Nature in particular pioneered the concept of AI-driven roleplay practice where a language model plays the role of a customer and provides feedback to a salesperson in real time — a concept that directly informs UpStat's voice training module.

The academic literature on AI-assisted coaching more broadly supports the effectiveness of immediate, specific, and repeated feedback as a mechanism for behaviour change (Kluger and DeNisi, 1996). The challenge in translating this to a software system is ensuring that AI-generated feedback is specific enough to be actionable and that the system creates a workflow in which managers actually engage with the feedback rather than ignoring it. UpStat's design addresses this through the "one critical error at a time" training plan structure: rather than overwhelming a manager with a comprehensive list of weaknesses, the system identifies the single most impactful correctable error from each call analysis and builds a practice session around that error alone.

The choice of OpenAI's GPT-4o model for the primary analysis pipeline reflects the need for sufficient reasoning capacity to evaluate nuanced conversational behaviour. Lighter models were considered for this task but produced inconsistent scoring quality during development testing. GPT-4o-mini is used for secondary tasks — extracting a training plan from an existing analysis text, identifying individual manager actions, and evaluating seller passport stage scores — where the inputs are more constrained and the reasoning demands are lower.

### 3.3 Voice AI and Conversational Analysis

Automatic speech recognition has undergone a step change in quality with the widespread adoption of transformer-based models, beginning with OpenAI Whisper in 2022 (Radford et al., 2022). Whisper demonstrated that a single model trained on a large multilingual corpus could achieve competitive transcription quality across languages including Russian, Kazakh-accented Russian, and other languages relevant to UpStat's target market.

UpStat's primary transcription provider is ElevenLabs Scribe v1, which offers speaker diarisation — the attribution of speech segments to distinct speakers — as part of its output. Speaker diarisation is essential for sales call analysis because distinguishing between manager and client utterances is a prerequisite for evaluating talk-to-listen ratios, identifying when the manager speaks over the client, and understanding the flow of a conversation. OpenAI Whisper is used as a fallback transcription provider when ElevenLabs is unavailable, though without diarisation capability in the fallback path.

The real-time voice coaching component of UpStat uses Microsoft Azure Voice Live API, a WebSocket-based service that provides bidirectional audio streaming with low latency, enabling a conversational AI interaction in which the sales manager speaks naturally and receives a spoken response. Azure Voice Live combines speech-to-text and text-to-speech into a single real-time session, avoiding the latency that would result from chaining separate STT and TTS API calls. This service is accessed via the `voice_assistant` module, which manages session lifecycle, injects a training-specific system prompt, and records the conversation to the database.

The academic literature on deliberate practice is relevant to the design rationale for voice training sessions (Ericsson et al., 1993). Deliberate practice theory holds that skill improvement requires not simply repeated exposure to a task but focused, effortful practice on specific components of performance with immediate feedback. The UpStat voice training module operationalises this by creating a practice scenario narrowly focused on the specific error identified in a manager's most recent call analysis, providing an AI interlocutor that responds realistically to the manager's conversational moves, and recording the session for subsequent review.

### 3.4 Web Application Architecture for SaaS Platforms

The architectural choices made in UpStat reflect a pragmatic approach to building a functional multi-tenant web application on limited development resources. The decision to use FastAPI as the backend framework reflects several considerations. FastAPI provides high performance relative to Django or Flask through its use of Python's async capabilities and the ASGI server Uvicorn (Ramírez, 2019). Its automatic OpenAPI documentation generation from type-annotated route handlers reduces the documentation burden during development. Its dependency injection system cleanly separates authentication, database access, and business logic in each route handler.

The choice of server-side rendering with Jinja2 templates rather than a single-page application framework such as React or Vue reflects a deliberate tradeoff. Server-side rendering reduces the complexity of the frontend codebase, eliminates the need for a separate API contract between frontend and backend state management, and produces pages that work correctly with browser navigation and bookmarking by default. For a data-heavy application where most interactions involve reading and submitting structured data rather than real-time collaborative editing, server-side rendering is a defensible choice that reduces overall system complexity (Osmani, 2022).

SQLAlchemy 2 was selected as the ORM layer because it provides a mature, well-documented abstraction over both SQLite and PostgreSQL, allowing the application to be developed and tested locally against SQLite and deployed against PostgreSQL without code changes. This is a common pattern for early-stage web applications where the development team needs the convenience of a zero-configuration local database alongside the scalability of a production-grade RDBMS. Alembic provides schema migration management, ensuring that database changes can be applied incrementally in production without data loss.

Docker and Docker Compose are used for deployment packaging. Containerisation ensures consistency between the development and production environments, simplifies dependency management, and enables the application to be deployed on any cloud platform that supports Docker. The use of Nginx as a reverse proxy provides SSL termination, static file serving, and request routing, following standard web application deployment patterns (Nginx, 2023).

---

## 4. Design and Methodology

### 4.1 Development Methodology

The project was developed using an iterative, feature-driven approach broadly consistent with Agile development principles. Rather than producing a comprehensive specification upfront and implementing it in a single pass, the team worked through successive development cycles, each focused on implementing, testing, and stabilising one or two major features before proceeding to the next.

The earliest development cycles established the core infrastructure: the FastAPI application skeleton, database schema, authentication flow, and the basic call upload and conversation display workflow. These foundational components were intentionally kept simple so that they could be validated quickly and extended without accumulating excessive technical debt. The call analysis pipeline was introduced in a second phase once the infrastructure was stable, beginning with the transcription step and progressively adding the checklist analysis, report generation, and parameter extraction layers.

Subsequent cycles introduced team management functionality, CRM integration, the analytics dashboard, the training plan system, and finally the Seller Passport, Manager Actions, and Owner Command Center features. This sequencing reflects an increasing level of product sophistication: each later feature depended on the data produced by earlier features. The Seller Passport, for example, requires that call analyses have already been run and that parameter values have been extracted and stored; the Owner Command Center aggregates those parameter values across a team.

Development was conducted with continuous reference to the production use of the system by early users. This meant that the team was responding to real feedback about missing functionality, confusing user interface elements, and performance issues throughout the development period, rather than discovering these problems only at the end. The consequence of this approach is that the codebase shows signs of organic growth — there are multiple pipeline variants, some redundant utility functions, and areas where the code could be refactored for clarity — but the functionality is grounded in actual user needs rather than speculative requirements.

### 4.2 System Architecture

UpStat follows a three-tier web application architecture: a Nginx reverse proxy tier, a FastAPI application tier, and a database tier. An additional set of external service integrations — OpenAI, ElevenLabs, Azure Voice Live, SMTP, and CRM webhooks — extends the backend into a distributed architecture for AI-heavy operations.

```
Browser / Client
        │
        ▼
  Nginx (Reverse Proxy, SSL, Static Files)
        │
        ▼
  FastAPI Application (Uvicorn, ASGI)
  ├── Jinja2 Template Rendering
  ├── 20+ Modular Routers
  ├── Service Layer (30+ services)
  ├── SQLAlchemy ORM
  │       │
  │       ▼
  │   SQLite (dev) / PostgreSQL (prod)
  │   Alembic Migrations
  │
  ├── Call Analysis Pipeline
  │       ├── ElevenLabs Scribe v1 (transcription)
  │       ├── OpenAI GPT-4o (analysis, report)
  │       └── OpenAI GPT-4o-mini (passport, actions)
  │
  ├── Voice Training Module
  │       └── Azure Voice Live (WebSocket)
  │
  ├── CRM Integration
  │       └── Bitrix24-compatible webhook
  │
  └── SMTP Email (pattern reports)
```

The FastAPI application is the central coordination point. It handles HTTP requests from browsers, orchestrates calls to external AI services, persists results to the database, and renders HTML responses via Jinja2. The application is structured as a collection of modular routers — each responsible for a specific functional domain — mounted on the main FastAPI application instance in `app/main.py`.

The call analysis pipeline is the most computationally intensive component. It is invoked synchronously within a request handler in the current implementation, which means that the HTTP response for a call upload is delayed until the full pipeline completes. This is a known architectural limitation discussed in Section 6.2. The pipeline produces results that are stored in the database and subsequently read by the analytics, dashboard, and owner reporting components.

The voice training module uses a separate WebSocket connection that is maintained for the duration of a training session. The browser's `voice-training.js` script captures microphone audio, sends it over the WebSocket to the FastAPI backend, and the backend relays it to Azure Voice Live. Responses from Azure are relayed back to the browser over the same WebSocket and played through the browser's audio output.

### 4.3 Database Design

The database schema comprises approximately 35 tables managed through 14 sequential Alembic migrations. The schema can be grouped into six functional areas.

**User and Authentication Tables**

The `users` table is the central identity record, storing email, bcrypt-hashed password (nullable for OAuth users), `google_id`, `role`, and premium flag. The `password_reset_tokens` table stores time-limited tokens for the password reset flow. The `team_invitations` table supports email-based team membership invitations.

**Team and Membership Tables**

The `teams` table records team name, owner (`manager_id`), description, and optional custom sales script. The `team_members` table represents the many-to-many relationship between users and teams, with a `role_in_team` column that currently supports `member`, `assistant_manager`, `manager`, and `owner` values. The `team_scripts` table stores versioned team-level sales scripts that are injected as context into call analysis.

**Call and Conversation Tables**

The `conversations` table is the primary record for each uploaded call, referencing the uploading user and team, and storing metadata such as file path, status, and creation timestamp. The `messages` table stores the structured dialogue turns extracted from transcription. The `attachments` table stores binary or text file references (transcripts, analysis reports). The `zoom_meetings` and `meeting_transcripts` tables, along with `custom_meetings` and `custom_meeting_participants`, support the Zoom and WebRTC meeting integration paths.

**Training Tables**

`analysis_training_plans` links a call analysis to its generated training plan. `trainings` records individual training tasks with checklist content, target skill area, and stage. `training_sessions` records each completed voice training session, linking it to a training task and a user. `voice_training_messages` stores the individual turns of a voice training conversation. `training_conversion_metrics` and `training_error_corrections` support advanced training analytics.

**Analytics and Scoring Tables**

`parameter_definitions` stores the catalogue of named evaluation parameters (e.g., `objection_handled`, `next_step_defined`, `needs_identified`). `parameter_values` stores the scored value for each parameter on each conversation. `checklist_item_definitions` and `checklist_item_scores` store checklist-level scores. `win_probability_scores` store the aggregate win probability calculated per conversation.

**CRM Tables**

`crm_integrations` stores the credentials and configuration for a team's CRM connection. `crm_recordings` tracks imported call recordings with their CRM source identifiers. `crm_manager_mappings` links CRM manager names to platform user accounts. `crm_deals`, `crm_leads`, `crm_contacts`, `crm_companies`, `crm_deal_products`, and `crm_activities` mirror the primary CRM entities for use in deal-value-weighted analytics.

**Seller Passport and Manager Actions Tables**

`seller_passports` stores the cumulative stage scores for each manager. `passport_snapshots` records point-in-time score states for trend tracking. `manager_actions` stores individual verbal actions extracted from each call. `action_patterns` aggregates recurring actions across a team. `analytics_messages` stores the AI-generated analytical messages from the owner analytics service.

The schema reflects a deliberate decision to keep related data together within the primary relational database rather than introducing a separate document store or time-series database. This simplifies operations and is appropriate for the current scale of the system.

### 4.4 Authentication and Authorization Design

UpStat uses cookie-based session authentication as its primary security mechanism. Upon successful login, the user's `user_id` is stored in a signed session cookie managed by Starlette's `SessionMiddleware`. The `SECRET_KEY` environment variable determines the signing key; this value must be set to a cryptographically random string in production.

The `require_user` dependency function in `app/deps.py` is used across all protected route handlers. It reads the `user_id` from the session, queries the database to confirm the user exists, and either returns the user object or redirects to the login page. This design ensures that any route handler that uses the `require_user` dependency cannot be accessed without a valid session, and that the user record is always available in the handler without an additional database query.

Password hashing uses bcrypt via the `passlib` library. Passwords are hashed at registration and at password change, and are never stored in plaintext. Password reset is implemented via time-limited tokens delivered by email; the token is stored hashed in `password_reset_tokens` and expires after a configurable interval.

Google OAuth is supported as an alternative sign-in method. The OAuth flow is implemented in `app/services/google_oauth.py` and follows the standard authorization code pattern: the user is redirected to Google's OAuth consent screen, Google redirects back with an authorization code, the backend exchanges the code for tokens, and the user's Google profile is used to locate or create a local user account.

**Role-Based Access Control** is implemented at two levels. The `users.role` field governs system-wide capabilities: `admin` users have access to the admin panel at `/admin/`; `manager` and `owner` users have access to standard dashboard functionality; `sale_manager` users are restricted to the `/sales/` path. Team-level roles are stored in `team_members.role_in_team` and govern access to team management functions such as inviting members, modifying team settings, and viewing the owner dashboard.

The owner dashboard access check in `owner_dashboard.py` verifies that the requesting user is either the `team.manager_id` or a member with `role_in_team` of `manager` or `owner`, or is a system-level admin.

JWT tokens are used in the Zoom Meeting SDK integration path (`app/services/signature_service.py`) to generate short-lived SDK signatures for browser-based Zoom meetings. This is a separate use of JWT tokens from user authentication.

### 4.5 API Design

UpStat's interface layer is primarily server-side rendered HTML rather than a JSON API. However, several routes return JSON responses for use by frontend JavaScript, particularly in the analytics dashboard, owner dashboard, and notification components. The following describes the organisation of the 20 router modules.

**Authentication Routes** (`/auth/`): login form submission, registration, password reset request, password reset confirmation, Google OAuth initiation and callback, logout.

**Public Routes** (`/public/`): landing page, pricing, public registration landing.

**Dashboard Routes** (`/dashboard/`): main dashboard view, conversation list, conversation detail, analysis result display.

**Chat Routes** (`/chat/`): AI chat interface for discussing an analysis with GPT-4o; trainer-specific variant (`/chat_trener/`).

**Settings Routes** (`/settings/`): user profile, password change, notification preferences.

**Team Routes** (`/teams/`): team creation, team settings, member management, invitations. Team analytics (`/team_analytics/`): per-member performance views, member report generation.

**Sales Routes** (`/sales/`): restricted to `sale_manager` role, providing manager-level call upload.

**Analytics Routes** (`/analytics/`): aggregated parameter analytics, checklist performance breakdown. Returns JSON fragments for dashboard AJAX updates.

**Owner Dashboard Routes** (`/owner/`): owner landing (redirects to first team), team-specific owner dashboard HTML, JSON data endpoint for dashboard widget updates. Three sub-endpoints correspond to the six dashboard modes.

**Training Plan Routes** (`/training_plans/`): training plan list, training detail, training session start, session completion.

**Voice Training Routes** (`/voice-training/`): session initialisation, WebSocket endpoint, session statistics, transcript retrieval.

**Zoom Meeting Routes** (`/zoom_meetings/`): meeting creation, SDK signature generation, meeting details.

**WebRTC Meeting Routes** (`/webrtc_meetings/`): custom meeting creation and management.

**CRM Integration Routes** (`/crm_integration/`): CRM connection setup, recording ingestion trigger, recording list, manager mapping management.

**Notification Routes** (`/notifications/`): notification list, mark-as-read.

**Progress Routes** (`/progress/`): manager progress tracking views.

**Performance Routes** (`/performance/`): performance summary endpoints.

**Admin Routes** (`/admin/`): user management, system statistics, prompt management.

**TTS Proxy Routes** (`/api/`): proxy for text-to-speech calls from the frontend to avoid exposing API keys in JavaScript.

### 4.6 AI Subsystem Design

The AI capabilities of UpStat are distributed across four distinct subsystems, each using a different model or service:

**Primary Call Analysis (GPT-4o)**

The call analysis pipeline is designed around a checklist-driven evaluation model. Checklists are defined as JSON files in the `checklists/` directory, each containing a set of yes/no or scored criteria corresponding to sales conversation behaviours (e.g., "Did the manager identify the client's primary concern before presenting a solution?"). The pipeline sends the full conversation transcript along with the checklist criteria to GPT-4o and requests a structured JSON response containing scores and rationale for each criterion.

Following the checklist evaluation, a second GPT-4o call generates the full coaching report using a configurable prompt managed through the `Prompt` model and `PromptService`. This separation allows the system prompt used for report generation to be updated through the admin interface without code changes. The model used for this call is `gpt-4o`.

**Parameter Extraction (GPT-4o-mini)**

After the primary analysis is complete, `parameter_extraction.py` makes a separate GPT-4o-mini call to extract numerical scores for a catalogue of named sales behaviour parameters. These parameters — such as `objection_handled`, `next_step_defined`, `needs_identified`, `price_devaluation`, `urgency_created`, and approximately twenty others — form the quantitative foundation for the analytics dashboard and owner reporting. GPT-4o-mini is used here because the extraction task is well-constrained: given an analysis text, score these named parameters from 0 to 1.

**Seller Passport and Training Plan (GPT-4o-mini)**

The seller passport scoring (`seller_passport_service.py`) uses GPT-4o-mini to evaluate stage-level scores across five phases of a sales conversation: contact, needs identification, presentation, objection handling, and closing. The training plan service (`training_plan_service.py`) uses GPT-4o-mini to identify the single most critical, correctable error from a call analysis text and extract it as a structured training task with a target stage and a practice checklist. Both tasks are suitable for GPT-4o-mini because the inputs are well-structured text derived from an already-completed GPT-4o analysis.

**Manager Actions Extraction (GPT-4o-mini)**

`manager_actions_service.py` uses GPT-4o-mini to identify two to six specific verbal actions from each call — concrete phrases or conversational moves categorised by sales stage, type, and whether they produced a positive or negative client reaction. These actions accumulate in the `manager_actions` table. The pattern detection logic runs in Python rather than in an LLM: actions are compared by textual similarity, and if more than sixty percent of the team's recent calls contain an action matching a given pattern, the pattern is flagged as `confirmed` and an email report is sent to the team owner.

**Real-Time Voice Coaching (Azure Voice Live)**

The voice training WebSocket handler connects the manager's browser audio stream to Azure Voice Live, a service that maintains a persistent, bidirectional audio session with an AI interlocutor. The system prompt injected into each session is constructed by combining a base coaching persona prompt with the specific training task content from the manager's current training record. This grounding ensures that the AI interlocutor focuses the conversation on the skill area identified in the manager's most recent call analysis.

---

## 5. Implementation

### 5.1 Backend Implementation

#### 5.1.1 Project Structure

The project is organised into two primary Python packages: the main `app` package and the `voice_assistant` package. The `app` package contains `main.py` (application factory and startup), `models.py` (all SQLAlchemy model definitions), `database.py` (engine and session factory), `deps.py` (shared FastAPI dependencies), and two sub-packages: `routers` (22 files) and `services` (34 files).

The `voice_assistant` package is structurally independent, with its own router, WebSocket handler, session manager, database service, and Azure integration logic. This separation was intentional: the voice training functionality has different operational characteristics (persistent WebSocket connections, different session lifecycle management) from the main HTTP-based application.

At the project root, additional components include the `alembic` package for migrations, `checklists` and `checklists_trener` directories for JSON evaluation criteria, a Telegram bot module (`bot.py`), the Docker and Nginx configuration files, and a large collection of documentation Markdown files generated during the development process.

#### 5.1.2 Business Logic

The business logic of UpStat is concentrated in the `services` layer. The most important service is `pipeline.py`, which orchestrates the full call analysis workflow:

1. The uploaded audio file is converted to WAV format using `ffmpeg` via a subprocess call.
2. The WAV file is submitted to ElevenLabs Scribe v1 for speaker-diarised transcription.
3. If ElevenLabs returns a 401 or 403 error, the system falls back to OpenAI Whisper for transcription without diarisation.
4. The transcription output is processed into a structured dialogue representation (`_words_to_turns`) that attributes each segment to either the manager or the client.
5. PII redaction is applied via `pii_redactor.py` to remove names, phone numbers, and other identifiable data from stored transcripts.
6. The dialogue is evaluated against the applicable JSON checklists, with GPT-4o scoring each criterion.
7. If the team has a custom sales script, it is injected as additional context for the analysis.
8. A full coaching report is generated via `PromptService` using the active `sales_audit_summary` prompt.
9. Post-processing steps run in sequence: `win_probability_service` calculates an overall deal win probability, `parameter_extraction` extracts named parameter scores, `update_seller_passport` updates the manager's passport, `process_manager_actions` extracts and accumulates verbal actions, `progress_tracker` updates manager progress records, and notifications are dispatched.

The `analytics_service.py` aggregates parameter values and checklist scores across multiple conversations for the team analytics dashboard. The `owner_analytics_service.py` extends this aggregation with CRM deal data to produce the revenue-weighted metrics displayed in the Owner Command Center.

#### 5.1.3 Authentication Flow

User registration creates a record in `users` with a bcrypt-hashed password and a default `role` of `user`. On login form submission, the `auth` router queries the user by email, verifies the submitted password against the stored hash using `passlib`, and on success writes the `user_id` to the Starlette session. The session is stored in a signed, server-side cookie.

Google OAuth registration follows the standard authorization code flow. The `google_oauth.py` service exchanges the authorization code for an access token, fetches the user's Google profile, and either creates a new user account (with a null `password_hash` and the `google_id` set) or links the Google identity to an existing account with the same email address.

The `require_user` dependency is used in almost every protected route. It reads `request.session.get("user_id")`, if absent redirects to `/login`, otherwise queries `db.get(User, user_id)` and returns the user object. Role checks for specific routes are implemented as additional conditional checks within the route handler or as separate dependency functions.

#### 5.1.4 Database Migrations and Persistence

Alembic manages schema evolution through 14 sequential migration scripts in `alembic/versions/`. Each migration is a pure Python script that defines `upgrade()` and `downgrade()` operations using SQLAlchemy's `op` API. The migration chain covers: initial schema (migration 001), Zoom meeting tables (002), user roles (003), CRM integration (004), voice training fields (005 and 007), WebRTC tables (006), CRM batch and scores (008), chat support (009), extended CRM entities (010), CRM soft disconnect (011), seller passport (012), manager actions (013), and owner role formalisation (014).

The application startup in `app/main.py` includes several supplementary schema checks for SQLite environments — manually creating or altering tables that may be missing from an older database. This pattern was adopted to handle cases where the SQLite development database had diverged from the migration state; it is not the recommended approach for production PostgreSQL deployments, where the Alembic migration chain should be run explicitly.

The SQLAlchemy session is managed per-request via a dependency generator that yields a session and ensures it is closed after the request completes, regardless of whether an exception was raised.

#### 5.1.5 External Service Integration

Integration with external services follows a consistent pattern: credentials are read from environment variables at startup, the integration is encapsulated in a service class or module, and the service is called from within the pipeline or a route handler.

The OpenAI integration uses the official `openai` Python library with the `AsyncOpenAI` or `OpenAI` client. All calls to GPT-4o and GPT-4o-mini pass the conversation content or analysis text as the user message, with a system message drawn from either the `PromptService` (for configurable prompts) or a hardcoded string (for simpler extraction tasks). Response parsing expects JSON output, enforced via an explicit instruction in the prompt.

ElevenLabs transcription uses the `requests` library to POST audio data to the ElevenLabs speech-to-text endpoint, parsing the word-level diarised output from the JSON response.

Azure Voice Live integration in `voice_assistant/azure_voice_live.py` establishes a WebSocket connection to the Azure endpoint, sends session configuration (voice model, system prompt), and relays audio frames bidirectionally.

CRM integration for Bitrix24 uses the Bitrix24 REST API via webhook URLs configured per team. The `crm_service.py` module handles entity synchronisation: recording retrieval, deal and contact import, and activity logging.

SMTP email is used in `manager_actions_service.py` to send pattern reports. The SMTP configuration (host, port, username, password) is read from environment variables via `email._get_smtp_config()`.

#### 5.1.6 Validation and Error Handling

Input validation for form submissions relies primarily on FastAPI's dependency system and explicit checks within route handlers. The `pipeline.py` service includes error handling at each stage: if transcription fails, the error is logged and the conversation status is set to `failed`; if GPT-4o returns malformed JSON, the pipeline catches the parse exception and attempts a fallback response format.

The `error_handler.py` service provides a centralised logging utility. JSON parsing errors from AI responses are handled by wrapping parse operations in try/except blocks and substituting a safe fallback value. Network errors from external service calls are not currently retried automatically; a failed pipeline run must be manually re-triggered by the user.

### 5.2 Frontend Implementation

#### 5.2.1 Component Structure

The frontend is built entirely with server-side rendered Jinja2 templates. The `_layout_dashboard.html` template provides the shared layout for all authenticated views, including the navigation sidebar, header, notification indicator, and JavaScript includes. Individual page templates extend this layout using Jinja2 block inheritance.

There are approximately 40 template files covering: the public landing page and authentication pages, the main dashboard and conversation views, team management pages, analytics and reporting pages, the training plan and voice training interfaces, CRM integration pages, the owner dashboard, and the admin panel. Each template directly receives the data it needs as Jinja2 context variables from the route handler, eliminating the need for client-side data fetching for initial page renders.

#### 5.2.2 State Management

Because the frontend uses server-side rendering, most application state is held in the database and rendered into HTML on each request. There is no client-side state management library. JavaScript on the page handles limited interactive behaviours: the mode switching in the owner dashboard, notification polling, progress bar animations, and the voice training WebSocket session.

The owner dashboard (`owner_dashboard.html`) is the most JavaScript-intensive page. It renders an initial dataset from the Jinja2 context and uses fetch API calls to `/api/owner/{team_id}/data` to reload dashboard data when the user changes the time filter or mode. The response is a JSON object that the JavaScript uses to update the DOM in-place.

#### 5.2.3 API Communication

JavaScript API communication is minimal and follows a simple pattern: fetch a JSON endpoint, parse the response, and update specific DOM elements. No Axios, SWR, or other HTTP client library is used. The `notifications.js` module polls the notification endpoint at a regular interval to display unread notification counts. The `progress-tracker.js` module fetches and renders a manager's progress data.

The `voice-training.js` module handles the most complex JavaScript interaction: it accesses the browser's microphone via `getUserMedia`, manages the WebSocket connection lifecycle, sends audio chunks to the backend, and plays back audio received from the backend. Error states (microphone permission denied, WebSocket disconnection) are handled and displayed to the user.

#### 5.2.4 Role-Based Interface

The Jinja2 templates conditionally render interface elements based on the authenticated user's role and team membership. Navigation items visible only to owners or admins are wrapped in Jinja2 `{% if %}` blocks that check the user role. The owner dashboard link in the sidebar is only rendered if the user has at least one team for which they hold owner or manager rights. Admin panel links are only rendered for users with `role == "admin"`.

This approach means that role-based access control is enforced at two levels: in the backend route handler (a non-owner user requesting the owner dashboard URL receives a 403 response or redirect), and in the template (the link to the owner dashboard is not rendered for non-owners, so users are not encouraged to attempt access they do not have).

#### 5.2.5 Key Pages

**Dashboard Page:** displays a chronological list of the user's uploaded conversations, with status indicators (processing, ready, failed), quick-access links to analysis results, and a summary of recent parameter scores.

**Conversation Detail Page:** shows the full conversation transcript with speaker attribution, the checklist scores panel, the generated coaching report, and links to the training plan generated from this analysis.

**Training Plan Page:** lists the manager's pending and completed training tasks, each describing the target skill and the specific error to practice. A training task can be activated to start a voice training session.

**Voice Training Page:** presents the training task context to the manager, controls for starting and stopping the microphone session, and a real-time transcript display updated as the session progresses.

**Analytics Page:** renders parameter score trends over time, a breakdown of checklist category performance, and comparison tables for team members where the user has appropriate access.

**Owner Dashboard Page:** the most information-dense page in the application, presenting the six-mode Command Center with revenue leak maps, AI-generated narrative summaries, and actionable recommendations. The page is dynamically updated by JavaScript when the user switches between modes or changes the time filter.

### 5.3 Deployment and DevOps

#### Docker and Docker Compose

The application is containerised using a `Dockerfile` based on `python:3.11-slim`. The image installs system dependencies including `ffmpeg` (required for audio conversion in the pipeline), copies the application source, installs Python dependencies from `requirements.txt`, and runs the application with Uvicorn:

```
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

The root `main.py` re-exports the FastAPI application from `app/main.py`, allowing the container entrypoint to reference the simpler path.

`docker-compose.yml` defines the primary `app` service. An override file (`docker-compose.override.yml`) provides development-specific settings such as volume mounts for live code reloading. The `ai_agent_service` and `sdk-runner` components have their own Dockerfiles and can be run as additional services in the compose configuration.

#### Environment Variables

All secrets and environment-specific configuration are managed through environment variables. The `env.example` file documents the required variables:

- `SECRET_KEY`: Starlette session signing key
- `DATABASE_URL`: SQLAlchemy database URL (sqlite or postgresql)
- `OPENAI_API_KEY`: OpenAI API key
- `ELEVENLABS_API_KEY`: ElevenLabs API key
- `AZURE_VOICE_LIVE_ENDPOINT`, `AZURE_VOICE_LIVE_KEY`: Azure credentials
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`: email configuration
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`: Google OAuth credentials
- `USE_AZURE_VOICE_LIVE`: boolean flag enabling/disabling the Azure voice feature

#### Nginx Configuration

The `nginx.conf` configures Nginx as a reverse proxy listening on port 80 (and 443 with SSL). Static files from `app/static/` are served directly by Nginx rather than proxied through the FastAPI application, reducing load on the application server. WebSocket connections to `/voice-training/ws` are proxied with appropriate `Upgrade` and `Connection` headers. Upload size limits are configured to accommodate audio file uploads.

#### Local Development Setup

For local development without Docker, the application can be run with `uvicorn app.main:app --reload` after creating a Python virtual environment and installing `requirements.txt`. SQLite is used as the development database, created automatically at `app.db`. Database migrations can be applied with `alembic upgrade head`.

### 5.4 Additional AI Services

#### Seller Passport Service

The Seller Passport feature provides a persistent performance profile for each sales manager. `seller_passport_service.py` implements two functions. `evaluate_stage_scores` submits the analysis report text to GPT-4o-mini with a prompt requesting numerical scores (0–100) for each of five sales stages: contact, needs identification, presentation, objection handling, and closing, plus an overall score. `update_seller_passport` resolves the identity of the manager from the conversation record, updates their `SellerPassport` record with the new scores (using a weighted average to smooth volatility), and writes a `PassportSnapshot` that records the scores at this point in time along with the delta relative to the previous snapshot. If the conversation was linked to a completed training task, the snapshot also records the training ID and the delta attributable to that training.

This design allows the system to answer the question: "did this manager's performance on the specific skill they practised actually improve after the training session?" The training delta provides a quantitative, if imprecise, answer to this question.

#### Manager Actions Service

`manager_actions_service.py` implements a bottom-up pattern detection approach. For each call analysis, GPT-4o-mini extracts two to six specific conversational actions — concrete phrases or moves — each annotated with the sales stage, action type (question, statement, objection handling move, closing attempt), polarity (positive or negative), and the client's observed reaction. These actions are stored as `ManagerAction` records.

After each new action is stored, the service checks whether any action pattern reaches a confirmation threshold. Patterns are grouped by textual similarity using a simple normalisation and matching approach rather than embedding-based similarity. If the proportion of team calls containing a given pattern exceeds sixty percent, the pattern status is updated from `collecting` to `confirmed` and an email report is generated for the team owner. This report lists confirmed patterns, identifies whether they are positive (to be reinforced) or negative (to be corrected), and provides example action texts.

#### Owner Analytics Service

`owner_analytics_service.py` aggregates data for the Owner Command Center. The `get_full_dashboard` method accepts a team ID and a time period (1, 7, or 30 days) and returns a dictionary containing:

- **Money leaks**: revenue impact estimates calculated by multiplying average parameter deficits by the team's CRM deal pipeline value. The parameters with the highest deficit × deal-value products are ranked as the top revenue leak sources.
- **Conversion patterns**: average parameter scores for closed-won versus closed-lost deals, identifying which parameters correlate with conversion.
- **Risk indicators**: proportion of conversations without confirmed next steps, without identified decision makers, and without defined timelines.
- **Speed metrics**: deal velocity indicators including urgency creation rate and closing timing scores.
- **Team performance**: per-member composite scores and ranking, derived from `SellerPassport` stage scores and recent parameter averages.
- **Forecast scenarios**: estimated revenue impact of improving the three highest-deficit parameters to the level of the team's top-performing member.

The AI narrative texts in the dashboard ("What I see", "Why this matters", "What I recommend") are generated from templates in the current implementation rather than through live LLM calls. This design decision was made to ensure consistent response times for the dashboard and to avoid LLM API costs on every page load. A future version could generate personalised narratives using an LLM when the dashboard data changes significantly.

#### Training Validator Service

`training_validator_service.py` provides a post-session evaluation function that analyses a completed voice training transcript against the training task's target checklist, producing a pass/fail assessment and a brief qualitative summary. This function is called after a voice training session completes and its result is stored with the `TrainingSession` record.

---

## 6. Results and Discussion

### 6.1 Achieved Results

Against the ten objectives stated in Section 2.2, the following results were achieved:

**O1 (Call Analysis Pipeline):** Fully implemented. The pipeline successfully processes audio uploads through ffmpeg, ElevenLabs transcription with speaker diarisation, checklist-based GPT-4o evaluation, and report generation. A text-input variant of the pipeline supports CRM-imported recordings where audio transcription has already been performed externally. All four pipeline variants (audio, text-file, raw-text, CRM-triggered) are functional.

**O2 (FastAPI Backend):** Fully implemented. The backend comprises 22 router modules covering all intended functional domains. The SQLAlchemy 2 persistence layer manages 35 database tables through Alembic migrations. The service layer contains 34 modules implementing the full range of business logic.

**O3 (Role-Based Access Control):** Fully implemented. Three system-level roles (`manager`/`user`, `owner`, `admin`) and four team-level roles (`member`, `assistant_manager`, `manager`, `owner`) are functional. Cookie-based session authentication, bcrypt password hashing, Google OAuth, and password reset via email are all working. Role-based route protection is applied consistently across the router layer.

**O4 (Server-Side Rendered Frontend):** Fully implemented. Approximately 40 Jinja2 templates cover all primary user workflows. The interface is functional across the primary use cases, though mobile responsiveness is incomplete (noted in Section 6.2).

**O5 (Seller Passport):** Fully implemented. The passport aggregates stage scores from GPT-4o-mini evaluations, tracks point-in-time snapshots, and records training-linked deltas. The owner can view team members' passport data through the team analytics interface.

**O6 (Manager Actions Collection):** Fully implemented. GPT-4o-mini extracts verbal actions from each call, the pattern detection logic accumulates and confirms patterns at the sixty-percent threshold, and SMTP email reports are sent to team owners when new patterns are confirmed.

**O7 (Real-Time Voice Coaching):** Implemented. The Azure Voice Live WebSocket integration is functional for teams with Azure credentials configured. The voice training module captures sessions to the database and the training validator service evaluates completed sessions. Known stability issues with WebSocket connection management are noted in Section 6.2.

**O8 (Owner Command Center):** Fully implemented. Six dashboard modes (Money, Conversion, Risk, Speed, Team, Forecast) are functional, with data aggregated from parameter values, seller passports, and CRM deal data. The JavaScript mode-switching interaction is smooth and the revenue leak estimates update correctly when the time filter changes.

**O9 (CRM Integration):** Implemented for Bitrix24-compatible APIs. The integration covers recording ingestion, deal/contact/company synchronisation, manager name mapping, and CRM-triggered pipeline execution. The scope of CRM entities supported is comprehensive, with six dedicated database tables mirroring CRM data structures.

**O10 (Containerised Deployment):** Fully implemented. The `Dockerfile`, `docker-compose.yml`, `nginx.conf`, and supporting scripts are complete. The `DOCKER_DEPLOYMENT.md` and `DEPLOYMENT_GUIDE_SCALABLE.md` documents provide step-by-step deployment instructions. The system has been successfully deployed on cloud virtual machines.

### 6.2 Limitations and Known Issues

**Synchronous Pipeline Execution:** The call analysis pipeline is currently invoked synchronously within the HTTP request handler. For a typical audio call of ten to fifteen minutes, the pipeline takes approximately sixty to ninety seconds to complete (transcription plus two or three GPT-4o calls). During this time, the web server process is blocked on this request. Under concurrent load, this will exhaust available worker processes quickly. The correct architectural solution — moving pipeline execution to a task queue such as Celery with Redis — was identified early but not implemented within the project timeline.

**No Automatic Retry for Failed Pipeline Steps:** If a GPT-4o or ElevenLabs API call fails due to a transient network error, the pipeline marks the conversation as failed and stops. The user must manually re-trigger the analysis. A robust implementation would include exponential backoff retries for transient errors.

**Voice Training WebSocket Stability:** The Azure Voice Live WebSocket connection occasionally drops during extended sessions, particularly in network environments with intermittent connectivity. The current error handling in `websocket_handler.py` logs the disconnection but does not automatically reconnect. Users must restart the session.

**Mobile Responsiveness:** The dashboard and analytics pages are designed for desktop viewports. While the landing page and authentication pages are mobile-responsive, the data-dense dashboard views are not usable on mobile devices without significant horizontal scrolling.

**AI Scoring Consistency:** GPT-4o-mini parameter scoring exhibits variance across runs on the same input. The scores for parameters such as `objection_handled` or `needs_clarity` can differ by five to fifteen percentage points between identical requests. This variance is partially mitigated by averaging multiple conversation scores in the analytics, but individual conversation scores should be treated as indicative rather than definitive.

**Security Gaps:** The application does not implement CSRF protection for form submissions. While the signed session cookie provides some protection, explicit CSRF tokens should be added. Rate limiting on the login endpoint is not implemented, making the login form vulnerable to brute-force attacks. These gaps are acceptable for a prototype but must be addressed before production deployment.

**SQLite Limitations in Development:** The SQLite development database does not support concurrent writes, which causes errors when multiple requests attempt to write to the database simultaneously (e.g., during pipeline execution with background processes). The PostgreSQL production configuration does not have this limitation.

**Partial Google OAuth Implementation:** Google OAuth sign-in is implemented but the account linking flow — connecting an existing email/password account to a Google identity — has edge cases that are not fully handled, particularly when the email already exists in the database with a different authentication method.

### 6.3 Discussion and Future Work

The most impactful architectural improvement for the next development cycle would be the introduction of an asynchronous task queue for pipeline execution. Replacing the synchronous pipeline call with a Celery task dispatched to a Redis broker would allow the HTTP request to return immediately with a "processing" status, and the pipeline would complete in the background. The frontend could poll for completion or receive a push notification via the existing notification system. This change would dramatically improve the scalability of the system under concurrent load without requiring changes to any other component.

The Manager Actions pattern detection system currently uses simple text normalisation for action grouping. A more sophisticated approach would use embedding-based similarity (OpenAI text-embedding-3-small) to cluster semantically similar actions even when they are phrased differently. This would improve the quality of pattern detection and reduce the manual effort of interpreting action pattern reports.

The Seller Passport's current score smoothing uses a fixed weighted average, which does not distinguish between a manager who has genuinely improved and one whose score fluctuates due to AI scoring variance. A more statistically sound approach would track the distribution of scores across multiple analyses rather than a single aggregate, and use change-point detection to identify genuine performance shifts.

The owner dashboard AI narrative generation currently uses static templates. Replacing these with a live GPT-4o-mini call when the dashboard data changes would allow the narrative to be genuinely responsive to the specific patterns in a team's recent data — for example, noting that a particular manager's performance dropped sharply in the current week, or that objection handling scores improved following a recent training cycle.

Future product directions that were discussed but not implemented include: a mobile application for managers to access their training plans and voice training sessions on smartphones; a public API with JWT authentication for third-party integrations; a WhatsApp bot interface for quick training reminders and progress updates; multi-language support for teams operating in languages other than Russian; and an automated A/B testing framework for prompt variations in the analysis pipeline.

---

## 7. Conclusion

This report has documented the complete design, implementation, and evaluation of UpStat, an AI-powered sales call analysis and coaching platform. The project was motivated by a genuine gap in available tools: the absence of affordable, configurable, locally deployable conversation analytics for sales teams in small and medium-sized enterprises, particularly in Russian-speaking markets where enterprise-grade tools such as Gong.io are both cost-prohibitive and contextually misaligned.

Chapter 2 established the problem context and defined ten specific technical objectives covering backend implementation, authentication, frontend development, AI integration, and deployment. Chapter 3 reviewed the relevant literature across sales performance management platforms, AI-driven coaching tools, voice AI technology, and web application architecture, situating UpStat within the current state of the field and justifying the technology stack.

Chapter 4 described the system design across six dimensions: the iterative development methodology, the three-tier web application architecture with external AI service integrations, a 35-table relational database schema organised into six functional groups, a cookie-session authentication system with role-based access control, a modular API layer with 20 router groups, and a four-part AI subsystem architecture using GPT-4o, GPT-4o-mini, ElevenLabs, and Azure Voice Live for distinct tasks.

Chapter 5 provided detailed implementation coverage of the FastAPI backend including the call analysis pipeline, authentication flow, database migrations, and external service integrations; the server-side rendered Jinja2 frontend with approximately 40 templates; the Docker-based deployment configuration; and three additional AI services — the Seller Passport, the Manager Actions system, and the Owner Analytics service.

Chapter 6 evaluated the results against the original objectives and found that all ten objectives were successfully implemented to at least a functional prototype level. The chapter was candid about known limitations including synchronous pipeline execution, AI scoring variance, incomplete mobile responsiveness, missing security hardening, and voice training connection stability. A realistic roadmap for future development was outlined, prioritising the asynchronous task queue as the most impactful architectural improvement.

The main aim of the project — to design and implement a web-based platform that automates sales call analysis, generates personalised coaching plans, and provides owners with a revenue-centric performance dashboard — has been achieved. UpStat is a working prototype that demonstrates the technical feasibility of the concept and provides a solid foundation for continued development toward a production-ready commercial product.

---

## 8. References

Ericsson, K. A., Krampe, R. T., & Tesch-Römer, C. (1993). The role of deliberate practice in the acquisition of expert performance. *Psychological Review*, 100(3), 363–406. https://doi.org/10.1037/0033-295X.100.3.363

FastAPI. (2023). *FastAPI documentation*. Sebastián Ramírez. https://fastapi.tiangolo.com

Gong.io. (2023). *Gong Reality Platform: Sales intelligence and conversation analytics*. Gong.io, Inc. https://www.gong.io

Greenberg, P. (2010). *CRM at the Speed of Light: Social CRM 2.0 Strategies, Tools, and Techniques for Engaging Your Customers* (4th ed.). McGraw-Hill.

Kluger, A. N., & DeNisi, A. (1996). The effects of feedback interventions on performance: A historical review, a meta-analysis, and a preliminary feedback intervention theory. *Psychological Bulletin*, 119(2), 254–284. https://doi.org/10.1037/0033-2909.119.2.254

Microsoft Azure. (2023). *Azure AI Speech: Voice Live API documentation*. Microsoft Corporation. https://learn.microsoft.com/en-us/azure/ai-services/speech-service/

Nginx. (2023). *Nginx documentation: Reverse proxy and load balancing*. F5, Inc. https://nginx.org/en/docs/

OpenAI. (2023). *GPT-4 technical report*. OpenAI. https://openai.com/research/gpt-4

OpenAI. (2023). *OpenAI API reference*. OpenAI. https://platform.openai.com/docs/api-reference

Osmani, A. (2022). *Rendering on the Web*. Google Developers Web Fundamentals. https://developers.google.com/web/updates/2019/02/rendering-on-the-web

Radford, A., Kim, J. W., Xu, T., Brockman, G., McLeavey, C., & Sutskever, I. (2022). *Robust speech recognition via large-scale weak supervision*. OpenAI. https://arxiv.org/abs/2212.04356

Ramírez, S. (2019). *FastAPI: Modern, fast (high-performance), web framework for building APIs with Python*. GitHub. https://github.com/tiangolo/fastapi

Rapp, A., Agnihotri, R., & Forbes, L. P. (2017). The next frontier: Artificial intelligence and sales. *Journal of Personal Selling and Sales Management*, 37(2), 77–79.

SQLAlchemy. (2023). *SQLAlchemy 2.0 documentation: ORM and core*. Mike Bayer. https://docs.sqlalchemy.org/en/20/

Second Nature. (2023). *AI sales training and roleplay platform*. Second Nature AI. https://secondnature.ai

Starlette. (2023). *Starlette: The little ASGI framework that shines*. Encode. https://www.starlette.io

Sybill.ai. (2023). *AI sales assistant: Call summaries and CRM updates*. Sybill Inc. https://www.sybill.ai

Wonderway. (2023). *AI sales coaching platform*. Wonderway AB. https://www.wonderway.io

ZoomInfo. (2023). *Chorus.ai: Conversation intelligence for sales teams*. ZoomInfo Technologies. https://www.zoominfo.com/products/chorus

---

## 9. Appendix

### Appendix A: Core Database Schema Excerpt (SQLAlchemy Models)

The following excerpt illustrates the primary entities and their relationships as defined in `app/models.py`.

```python
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=True)  # nullable for OAuth users
    google_id = Column(String(255), nullable=True, unique=True)
    role = Column(String(20), default="user")  # user | manager | admin | sale_manager
    is_premium = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    conversations = relationship("Conversation", back_populates="user")
    team_memberships = relationship("TeamMember", back_populates="user")

class Team(Base):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    manager_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    members = relationship("TeamMember", back_populates="team")
    crm_integration = relationship("CRMIntegration", back_populates="team", uselist=False)

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    file_path = Column(String(512), nullable=True)
    status = Column(String(50), default="pending")  # pending | processing | ready | failed
    created_at = Column(DateTime, default=datetime.utcnow)
    messages = relationship("Message", back_populates="conversation")
    attachments = relationship("Attachment", back_populates="conversation")
    parameter_values = relationship("ParameterValue", back_populates="conversation")

class SellerPassport(Base):
    __tablename__ = "seller_passports"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    stage_contact = Column(Float, default=0.0)
    stage_needs = Column(Float, default=0.0)
    stage_presentation = Column(Float, default=0.0)
    stage_objections = Column(Float, default=0.0)
    stage_closing = Column(Float, default=0.0)
    overall_score = Column(Float, default=0.0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    snapshots = relationship("PassportSnapshot", back_populates="passport")
```

### Appendix B: Call Analysis Pipeline — Core Flow

The following excerpt from `app/services/pipeline.py` illustrates the top-level pipeline orchestration function.

```python
async def run_pipeline(conversation_id: int, db: Session) -> None:
    conversation = db.get(Conversation, conversation_id)
    conversation.status = "processing"
    db.commit()

    try:
        # Step 1: Convert audio to WAV
        wav_path = convert_to_wav(conversation.file_path)

        # Step 2: Transcribe with speaker diarisation
        try:
            transcript_data = await transcribe_elevenlabs(wav_path)
            dialogue = words_to_turns(transcript_data)
        except ElevenLabsAuthError:
            transcript_data = await transcribe_whisper(wav_path)
            dialogue = fallback_turns(transcript_data)

        # Step 3: PII redaction
        dialogue = redact_pii(dialogue)

        # Step 4: Save transcript
        save_transcript(conversation, dialogue, db)

        # Step 5: Checklist evaluation
        checklist = load_checklist(conversation.team_id)
        checklist_scores = await evaluate_checklist(dialogue, checklist)

        # Step 6: Generate coaching report
        prompt = PromptService.get_active(db, "sales_audit_summary")
        report = await generate_report(dialogue, checklist_scores, prompt)
        save_attachment(conversation, report, "analysis_report", db)

        # Step 7: Post-processing
        await win_probability_service.calculate(conversation, checklist_scores, db)
        await parameter_extraction.extract(conversation, report, db)
        await seller_passport_service.update_seller_passport(conversation, report, db)
        await manager_actions_service.process_manager_actions(conversation, report, db)
        progress_tracker.update(conversation, db)
        notification_service.dispatch(conversation, db)

        conversation.status = "ready"
    except Exception as e:
        logger.error(f"Pipeline failed for conversation {conversation_id}: {e}")
        conversation.status = "failed"
    finally:
        db.commit()
```

### Appendix C: Authentication Dependency

The `require_user` dependency function used across all protected routes, from `app/deps.py`.

```python
from fastapi import Request, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User

async def require_user(
    request: Request,
    db: Session = Depends(get_db)
) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=307,
            headers={"Location": "/login"}
        )
    user = db.get(User, user_id)
    if not user:
        request.session.clear()
        raise HTTPException(
            status_code=307,
            headers={"Location": "/login"}
        )
    return user

async def require_admin(
    current_user: User = Depends(require_user)
) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user
```

### Appendix D: Owner Dashboard Data Endpoint

The JSON data endpoint that powers the owner dashboard's JavaScript mode-switching, from `app/routers/owner_dashboard.py`.

```python
@router.get("/api/owner/{team_id}/data")
async def get_owner_dashboard_data(
    team_id: int,
    days: int = Query(default=30, ge=1, le=90),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    # Verify access
    owner_teams = _get_owner_teams(current_user, db)
    team_ids = [t.id for t in owner_teams]
    if team_id not in team_ids and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Access denied")

    # Fetch aggregated dashboard data
    analytics_service = OwnerAnalyticsService(db)
    dashboard_data = analytics_service.get_full_dashboard(
        team_id=team_id,
        days=days
    )

    return JSONResponse(content=dashboard_data)
```

---

*End of Diploma Report — UpStat: An AI-Powered Sales Call Analysis and Manager Coaching Platform*
