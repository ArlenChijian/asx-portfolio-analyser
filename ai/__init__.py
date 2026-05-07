"""AI integration layer.

Two features:
    parse_profile  - free-text user description -> structured UserProfile fields
    explain        - PortfolioResult -> 2-3 paragraph plain-English summary

Both use Claude Haiku 4.5 by default for cost. Both fail gracefully if
the ANTHROPIC_API_KEY environment variable is not set; callers should
treat AI features as optional enhancements rather than hard dependencies.
"""
