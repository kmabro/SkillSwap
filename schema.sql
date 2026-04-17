-- MySQL schema aligned with the current SkillSwap Flask models.
CREATE DATABASE IF NOT EXISTS skillswap CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE skillswap;

SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS notifications;
DROP TABLE IF EXISTS messages;
DROP TABLE IF EXISTS user_reports;
DROP TABLE IF EXISTS ratings;
DROP TABLE IF EXISTS requests;
DROP TABLE IF EXISTS user_skills;
DROP TABLE IF EXISTS user_skills_wanted;
DROP TABLE IF EXISTS user_skills_offered;
DROP TABLE IF EXISTS skills;
DROP TABLE IF EXISTS categories;
DROP TABLE IF EXISTS users;

SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE users (
    user_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    username VARCHAR(40) NOT NULL,
    email VARCHAR(120) NOT NULL,
    password VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
    availability BOOLEAN NOT NULL DEFAULT TRUE,
    availability_status VARCHAR(20) NOT NULL DEFAULT 'Available',
    profile_image VARCHAR(255) NULL,
    bio TEXT NULL,
    location VARCHAR(120) NULL,
    show_email_on_profile BOOLEAN NOT NULL DEFAULT FALSE,
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    otp_code TEXT NULL,
    otp_expiry DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at DATETIME NULL,
    UNIQUE KEY uq_users_username (username),
    UNIQUE KEY uq_users_email (email),
    KEY ix_users_username (username)
);

CREATE TABLE categories (
    category_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(80) NOT NULL,
    UNIQUE KEY uq_categories_name (name),
    KEY ix_categories_name (name)
);

CREATE TABLE skills (
    skill_id INT AUTO_INCREMENT PRIMARY KEY,
    skill_name VARCHAR(120) NOT NULL,
    category_id INT NULL,
    category VARCHAR(80) NULL,
    description VARCHAR(255) NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    UNIQUE KEY uq_skills_name (skill_name),
    KEY ix_skills_name (skill_name),
    KEY ix_skills_category_id (category_id),
    CONSTRAINT fk_skills_category FOREIGN KEY (category_id) REFERENCES categories(category_id)
);

CREATE TABLE user_skills_offered (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    skill_id INT NOT NULL,
    level VARCHAR(20) NOT NULL DEFAULT 'Intermediate',
    UNIQUE KEY uq_offered (user_id, skill_id),
    CONSTRAINT fk_offered_user FOREIGN KEY (user_id) REFERENCES users(user_id),
    CONSTRAINT fk_offered_skill FOREIGN KEY (skill_id) REFERENCES skills(skill_id)
);

CREATE TABLE user_skills_wanted (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    skill_id INT NOT NULL,
    level VARCHAR(20) NOT NULL DEFAULT 'Beginner',
    UNIQUE KEY uq_wanted (user_id, skill_id),
    CONSTRAINT fk_wanted_user FOREIGN KEY (user_id) REFERENCES users(user_id),
    CONSTRAINT fk_wanted_skill FOREIGN KEY (skill_id) REFERENCES skills(skill_id)
);

CREATE TABLE user_skills (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    skill_id INT NOT NULL,
    UNIQUE KEY uq_user_skills_user_skill (user_id, skill_id),
    KEY ix_user_skills_user_id (user_id),
    KEY ix_user_skills_skill_id (skill_id),
    CONSTRAINT fk_user_skills_user FOREIGN KEY (user_id) REFERENCES users(user_id),
    CONSTRAINT fk_user_skills_skill FOREIGN KEY (skill_id) REFERENCES skills(skill_id)
);

CREATE TABLE requests (
    request_id INT AUTO_INCREMENT PRIMARY KEY,
    sender_id INT NOT NULL,
    receiver_id INT NOT NULL,
    offered_skill_id INT NOT NULL,
    requested_skill_id INT NOT NULL,
    final_offered_skill_id INT NULL,
    final_requested_skill_id INT NULL,
    status VARCHAR(40) NOT NULL DEFAULT 'pending',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    sender_rated BOOLEAN NOT NULL DEFAULT FALSE,
    receiver_rated BOOLEAN NOT NULL DEFAULT FALSE,
    is_completed_by_sender BOOLEAN NOT NULL DEFAULT FALSE,
    is_completed_by_receiver BOOLEAN NOT NULL DEFAULT FALSE,
    sender_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    receiver_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    rated_by_sender BOOLEAN NOT NULL DEFAULT FALSE,
    rated_by_receiver BOOLEAN NOT NULL DEFAULT FALSE,
    session_room VARCHAR(120) NULL,
    session_link VARCHAR(255) NULL,
    session_scheduled_for DATETIME NULL,
    session_proposed_by INT NULL,
    session_confirmed_at DATETIME NULL,
    session_started_at DATETIME NULL,
    session_completed_at DATETIME NULL,
    CONSTRAINT ck_request_status CHECK (status IN ('pending', 'countered', 'accepted', 'rejected', 'awaiting_confirmation', 'completed')),
    KEY ix_requests_status (status),
    CONSTRAINT fk_req_sender FOREIGN KEY (sender_id) REFERENCES users(user_id),
    CONSTRAINT fk_req_receiver FOREIGN KEY (receiver_id) REFERENCES users(user_id),
    CONSTRAINT fk_req_offered_skill FOREIGN KEY (offered_skill_id) REFERENCES skills(skill_id),
    CONSTRAINT fk_req_requested_skill FOREIGN KEY (requested_skill_id) REFERENCES skills(skill_id),
    CONSTRAINT fk_req_final_offered_skill FOREIGN KEY (final_offered_skill_id) REFERENCES skills(skill_id),
    CONSTRAINT fk_req_final_requested_skill FOREIGN KEY (final_requested_skill_id) REFERENCES skills(skill_id),
    CONSTRAINT fk_req_session_proposed_by FOREIGN KEY (session_proposed_by) REFERENCES users(user_id)
);

CREATE TABLE ratings (
    rating_id INT AUTO_INCREMENT PRIMARY KEY,
    from_user INT NOT NULL,
    to_user INT NOT NULL,
    exchange_request_id INT NULL,
    rating INT NOT NULL,
    feedback TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_rating_value CHECK (rating >= 1 AND rating <= 5),
    UNIQUE KEY uq_rating_exchange_from_user (exchange_request_id, from_user),
    KEY ix_ratings_exchange_request_id (exchange_request_id),
    CONSTRAINT fk_rating_from FOREIGN KEY (from_user) REFERENCES users(user_id),
    CONSTRAINT fk_rating_to FOREIGN KEY (to_user) REFERENCES users(user_id),
    CONSTRAINT fk_rating_exchange_request FOREIGN KEY (exchange_request_id) REFERENCES requests(request_id)
);

CREATE TABLE user_reports (
    report_id INT AUTO_INCREMENT PRIMARY KEY,
    reporter_id INT NOT NULL,
    reported_user_id INT NOT NULL,
    reason VARCHAR(40) NOT NULL,
    description TEXT NULL,
    report_attachments TEXT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY ix_user_reports_status (status),
    CONSTRAINT fk_report_reporter FOREIGN KEY (reporter_id) REFERENCES users(user_id),
    CONSTRAINT fk_report_reported_user FOREIGN KEY (reported_user_id) REFERENCES users(user_id)
);

CREATE TABLE messages (
    message_id INT AUTO_INCREMENT PRIMARY KEY,
    sender_id INT NOT NULL,
    receiver_id INT NOT NULL,
    message TEXT NOT NULL,
    message_type VARCHAR(20) NOT NULL DEFAULT 'user',
    attachment_url TEXT NULL,
    attachment_type TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at DATETIME NULL,
    CONSTRAINT fk_message_sender FOREIGN KEY (sender_id) REFERENCES users(user_id),
    CONSTRAINT fk_message_receiver FOREIGN KEY (receiver_id) REFERENCES users(user_id)
);

CREATE TABLE notifications (
    notification_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    message VARCHAR(255) NOT NULL,
    notif_type VARCHAR(40) NOT NULL,
    link VARCHAR(255) NULL,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_notification_user FOREIGN KEY (user_id) REFERENCES users(user_id)
);
