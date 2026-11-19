locals {
  frontend_origin_id = "frontend"
  banners_origin_id  = "${split(":", var.assets_bucket_arn)[5]}-banners"
  frontend_url       = "https://${aws_cloudfront_distribution.this.domain_name}"
  cognito_issuer     = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.this.id}"

  callback_urls = concat(
    ["${local.frontend_url}/auth/callback"],
    var.allow_localhost_callbacks ? [
      "http://localhost:8080/auth/callback",
      "http://localhost:5173/auth/callback"
    ] : []
  )

  logout_urls = concat(
    ["${local.frontend_url}/"],
    var.allow_localhost_callbacks ? [
      "http://localhost:8080/",
      "http://localhost:5173/"
    ] : []
  )
}

resource "aws_s3_bucket" "frontend" {
  bucket        = "${var.name_prefix}-frontend-${var.aws_account_id}-${var.aws_region}"
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${var.name_prefix}-frontend"
  description                       = "Signed CloudFront reads from the private frontend bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_origin_access_control" "banners" {
  name                              = "${var.name_prefix}-banners"
  description                       = "Signed CloudFront reads restricted to the banners prefix by the assets bucket policy"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_function" "spa" {
  name    = "${var.name_prefix}-frontend-spa"
  runtime = "cloudfront-js-2.0"
  comment = "Serve the frontend entry point for routes such as /auth/callback"
  publish = true
  code    = file("${path.module}/spa.js")
}

resource "aws_cloudfront_cache_policy" "frontend" {
  name        = "${var.name_prefix}-frontend"
  comment     = "Honor no-cache on index.html and cache immutable frontend assets"
  default_ttl = 60
  min_ttl     = 0
  max_ttl     = 31536000

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true

    cookies_config {
      cookie_behavior = "none"
    }

    headers_config {
      header_behavior = "none"
    }

    query_strings_config {
      query_string_behavior = "none"
    }
  }
}

data "aws_cloudfront_cache_policy" "banners" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_response_headers_policy" "security" {
  name = "Managed-SecurityHeadersPolicy"
}

resource "aws_cloudfront_distribution" "this" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${var.name_prefix} frontend and public event banners"
  default_root_object = "index.html"
  http_version        = "http2and3"
  price_class         = "PriceClass_100"
  wait_for_deployment = true

  origin {
    origin_id                = local.frontend_origin_id
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id

    s3_origin_config {
      origin_access_identity = ""
    }
  }

  origin {
    origin_id                = local.banners_origin_id
    domain_name              = var.assets_bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.banners.id

    s3_origin_config {
      origin_access_identity = ""
    }
  }

  default_cache_behavior {
    target_origin_id           = local.frontend_origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = aws_cloudfront_cache_policy.frontend.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security.id

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.spa.arn
    }
  }

  ordered_cache_behavior {
    path_pattern               = "/banners/*"
    target_origin_id           = local.banners_origin_id
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.banners.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

data "aws_iam_policy_document" "frontend" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.frontend.arn, "${aws_s3_bucket.frontend.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid       = "AllowCloudFrontFrontendRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.frontend.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.this.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = data.aws_iam_policy_document.frontend.json

  depends_on = [aws_s3_bucket_public_access_block.frontend]
}

resource "aws_cognito_user_pool" "this" {
  name                     = "${var.name_prefix}-users"
  user_pool_tier           = "LITE"
  deletion_protection      = "INACTIVE"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OFF"

  username_configuration {
    case_sensitive = false
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  verification_message_template {
    default_email_option = "CONFIRM_WITH_CODE"
  }

  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }
}

resource "aws_cognito_resource_server" "api" {
  user_pool_id = aws_cognito_user_pool.this.id
  identifier   = "flash-ticket"
  name         = "Flash Ticket API"

  scope {
    scope_name        = "bookings:write"
    scope_description = "Create a booking and read or download the authenticated user's own tickets"
  }
}

resource "aws_cognito_user_pool_domain" "this" {
  domain                = "${var.name_prefix}-${var.aws_account_id}-${var.aws_region}"
  user_pool_id          = aws_cognito_user_pool.this.id
  managed_login_version = 1
}

resource "aws_cognito_user_pool_client" "frontend" {
  name                                 = "${var.name_prefix}-frontend"
  user_pool_id                         = aws_cognito_user_pool.this.id
  generate_secret                      = false
  supported_identity_providers         = ["COGNITO"]
  allowed_oauth_flows_user_pool_client  = true
  allowed_oauth_flows                   = ["code"]
  allowed_oauth_scopes                  = ["openid", "email", "profile", "${aws_cognito_resource_server.api.identifier}/bookings:write"]
  callback_urls                        = local.callback_urls
  logout_urls                          = local.logout_urls
  default_redirect_uri                 = "${local.frontend_url}/auth/callback"
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH"]
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  access_token_validity                = 15
  id_token_validity                    = 15
  refresh_token_validity               = 1
  auth_session_validity                = 3
  read_attributes                     = ["email", "email_verified"]
  write_attributes                    = ["email"]

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }

  depends_on = [aws_cognito_resource_server.api]
}
