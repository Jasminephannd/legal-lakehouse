resource "aws_ecr_repository" "parser" {
  name                 = "legal-lakehouse-parser"
  image_tag_mutability = "IMMUTABLE" # tags are commit SHAs (Block 5/Day 2 CD) — never overwritten

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "parser" {
  repository = aws_ecr_repository.parser.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 5 images"
        selection = {
          tagStatus     = "any"
          countType     = "imageCountMoreThan"
          countNumber   = 5
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

output "ecr_repository_url" {
  value       = aws_ecr_repository.parser.repository_url
  description = "Used by Block 5's docker build/push and by the Lambda function's image_uri."
}
