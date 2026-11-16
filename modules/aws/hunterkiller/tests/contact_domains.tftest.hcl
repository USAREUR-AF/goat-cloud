# Plan only, mocked providers: no credentials are used and no API is called.

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }

  mock_data "aws_region" {
    defaults = {
      name   = "us-east-1"
      region = "us-east-1"
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}
mock_provider "null" {}

variables {
  project                          = "example"
  environment                      = "dev"
  compliance_reports_bucket        = "example-reports"
  compliance_scanner_function_name = "example-scanner"
  dashboard_url                    = "https://compliance.example.com"
  ses_admin_emails                 = ["Admin@Example.com", "ops@example.org", "second@example.com"]
}

run "defaults_to_the_admin_domains" {
  command = plan

  assert {
    condition     = aws_lambda_function.notifier.environment[0].variables["CONTACT_ALLOWED_DOMAINS"] == "example.com,example.org"
    error_message = "With no list given, contacts are limited to the domains of the admin addresses."
  }
}

run "explicit_list_is_used" {
  command = plan

  variables {
    contact_allowed_email_domains = ["example.net"]
  }

  assert {
    condition     = aws_lambda_function.notifier.environment[0].variables["CONTACT_ALLOWED_DOMAINS"] == "example.net"
    error_message = "A given list is used as it is."
  }
}

run "empty_list_accepts_any_domain" {
  command = plan

  variables {
    contact_allowed_email_domains = []
  }

  assert {
    condition     = aws_lambda_function.notifier.environment[0].variables["CONTACT_ALLOWED_DOMAINS"] == ""
    error_message = "An empty list states that any domain is accepted."
  }
}
