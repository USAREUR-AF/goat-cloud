# The token is read from the environment variable GITHUB_TOKEN. It needs
# administration rights on the repository; it is used by this optional root
# only, never by the workflows.
provider "github" {
  owner = var.github_org
}
