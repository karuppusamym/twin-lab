/*
    QTT - Quality Tracking Tool
    Database: QTT_APP   Schema: dbo
    Table definitions as exported for the application team (SQL Server 2019).
    Synthetic sample; no data included.
*/
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO

CREATE TABLE [dbo].[users](
    [user_id] [int] IDENTITY(1,1) NOT NULL,
    [username] [nvarchar](50) NOT NULL,
    [full_name] [nvarchar](100) NOT NULL,
    [email] [nvarchar](254) NOT NULL,
    [role] [nvarchar](20) NOT NULL,
    [password_hash] [nvarchar](200) NOT NULL,
    [is_active] [bit] NOT NULL,
    [created_at] [datetime2](0) NOT NULL,
 CONSTRAINT [PK_users] PRIMARY KEY CLUSTERED
(
    [user_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_users_username] UNIQUE NONCLUSTERED
(
    [username] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
GO

ALTER TABLE [dbo].[users] ADD CONSTRAINT [DF_users_is_active] DEFAULT ((1)) FOR [is_active]
GO
ALTER TABLE [dbo].[users] WITH CHECK ADD CONSTRAINT [CK_users_role] CHECK (([role]=N'REVIEWER' OR [role]=N'ANALYST'))
GO

CREATE TABLE [dbo].[loans](
    [loan_id] [int] IDENTITY(1,1) NOT NULL,
    [loan_no] [nvarchar](20) NOT NULL,
    [borrower_name] [nvarchar](120) NOT NULL,
    [co_borrower_name] [nvarchar](120) NULL,
    [product_code] [nvarchar](10) NOT NULL,
    [loan_amount] [decimal](12, 2) NOT NULL,
    [interest_rate] [decimal](6, 3) NOT NULL,
    [closing_date] [date] NOT NULL,
    [branch_code] [nvarchar](10) NOT NULL,
    [branch_name] [nvarchar](80) NOT NULL,
    [loan_officer] [nvarchar](100) NOT NULL,
    [property_state] [nchar](2) NOT NULL,
    [created_at] [datetime2](0) NOT NULL,
 CONSTRAINT [PK_loans] PRIMARY KEY CLUSTERED
(
    [loan_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_loans_loan_no] UNIQUE NONCLUSTERED
(
    [loan_no] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
GO

CREATE TABLE [dbo].[qc_reviews](
    [review_id] [int] IDENTITY(1,1) NOT NULL,
    [review_no] [nvarchar](20) NOT NULL,
    [loan_id] [int] NOT NULL,
    [review_type] [nvarchar](40) NOT NULL,
    [status] [nvarchar](20) NOT NULL,
    [assigned_to] [int] NULL,
    [reviewer_id] [int] NULL,
    [due_date] [date] NOT NULL,
    [return_reason] [nvarchar](500) NULL,
    [submitted_at] [datetime2](0) NULL,
    [approved_at] [datetime2](0) NULL,
    [created_at] [datetime2](0) NOT NULL,
    [updated_at] [datetime2](0) NOT NULL,
 CONSTRAINT [PK_qc_reviews] PRIMARY KEY CLUSTERED
(
    [review_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_qc_reviews_review_no] UNIQUE NONCLUSTERED
(
    [review_no] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
GO

ALTER TABLE [dbo].[qc_reviews] WITH CHECK ADD CONSTRAINT [FK_qc_reviews_loans] FOREIGN KEY([loan_id])
REFERENCES [dbo].[loans] ([loan_id])
GO
ALTER TABLE [dbo].[qc_reviews] CHECK CONSTRAINT [FK_qc_reviews_loans]
GO
ALTER TABLE [dbo].[qc_reviews] WITH CHECK ADD CONSTRAINT [FK_qc_reviews_users_assigned] FOREIGN KEY([assigned_to])
REFERENCES [dbo].[users] ([user_id])
GO
ALTER TABLE [dbo].[qc_reviews] CHECK CONSTRAINT [FK_qc_reviews_users_assigned]
GO
ALTER TABLE [dbo].[qc_reviews] WITH CHECK ADD CONSTRAINT [FK_qc_reviews_users_reviewer] FOREIGN KEY([reviewer_id])
REFERENCES [dbo].[users] ([user_id])
GO
ALTER TABLE [dbo].[qc_reviews] CHECK CONSTRAINT [FK_qc_reviews_users_reviewer]
GO
ALTER TABLE [dbo].[qc_reviews] WITH CHECK ADD CONSTRAINT [CK_qc_reviews_status] CHECK (([status]=N'APPROVED' OR [status]=N'RETURNED' OR [status]=N'SUBMITTED' OR [status]=N'IN_PROGRESS' OR [status]=N'OPEN'))
GO

CREATE NONCLUSTERED INDEX [IX_qc_reviews_status] ON [dbo].[qc_reviews]
(
    [status] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
CREATE NONCLUSTERED INDEX [IX_qc_reviews_assigned_to] ON [dbo].[qc_reviews]
(
    [assigned_to] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO

CREATE TABLE [dbo].[checklist_items](
    [checklist_item_id] [int] IDENTITY(1,1) NOT NULL,
    [item_code] [nvarchar](10) NOT NULL,
    [category] [nvarchar](20) NOT NULL,
    [description] [nvarchar](200) NOT NULL,
    [guidance] [nvarchar](500) NULL,
    [sort_order] [int] NOT NULL,
    [is_active] [bit] NOT NULL,
 CONSTRAINT [PK_checklist_items] PRIMARY KEY CLUSTERED
(
    [checklist_item_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_checklist_items_item_code] UNIQUE NONCLUSTERED
(
    [item_code] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
GO

ALTER TABLE [dbo].[checklist_items] ADD CONSTRAINT [DF_checklist_items_is_active] DEFAULT ((1)) FOR [is_active]
GO
ALTER TABLE [dbo].[checklist_items] WITH CHECK ADD CONSTRAINT [CK_checklist_items_category] CHECK (([category]=N'Compliance' OR [category]=N'Collateral' OR [category]=N'Income' OR [category]=N'Credit'))
GO

CREATE TABLE [dbo].[review_items](
    [review_item_id] [int] IDENTITY(1,1) NOT NULL,
    [review_id] [int] NOT NULL,
    [checklist_item_id] [int] NOT NULL,
    [result] [nvarchar](4) NULL,
    [comment] [nvarchar](500) NULL,
    [updated_by] [int] NULL,
    [updated_at] [datetime2](0) NULL,
 CONSTRAINT [PK_review_items] PRIMARY KEY CLUSTERED
(
    [review_item_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY],
 CONSTRAINT [UQ_review_items_review_checklist] UNIQUE NONCLUSTERED
(
    [review_id] ASC,
    [checklist_item_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
GO

ALTER TABLE [dbo].[review_items] WITH CHECK ADD CONSTRAINT [FK_review_items_qc_reviews] FOREIGN KEY([review_id])
REFERENCES [dbo].[qc_reviews] ([review_id])
GO
ALTER TABLE [dbo].[review_items] CHECK CONSTRAINT [FK_review_items_qc_reviews]
GO
ALTER TABLE [dbo].[review_items] WITH CHECK ADD CONSTRAINT [FK_review_items_checklist_items] FOREIGN KEY([checklist_item_id])
REFERENCES [dbo].[checklist_items] ([checklist_item_id])
GO
ALTER TABLE [dbo].[review_items] CHECK CONSTRAINT [FK_review_items_checklist_items]
GO
ALTER TABLE [dbo].[review_items] WITH CHECK ADD CONSTRAINT [FK_review_items_users] FOREIGN KEY([updated_by])
REFERENCES [dbo].[users] ([user_id])
GO
ALTER TABLE [dbo].[review_items] CHECK CONSTRAINT [FK_review_items_users]
GO
ALTER TABLE [dbo].[review_items] WITH CHECK ADD CONSTRAINT [CK_review_items_result] CHECK (([result] IS NULL OR [result]=N'NA' OR [result]=N'FAIL' OR [result]=N'PASS'))
GO

CREATE TABLE [dbo].[review_audit](
    [audit_id] [bigint] IDENTITY(1,1) NOT NULL,
    [review_id] [int] NOT NULL,
    [event] [nvarchar](30) NOT NULL,
    [from_status] [nvarchar](20) NULL,
    [to_status] [nvarchar](20) NULL,
    [actor] [nvarchar](50) NOT NULL,
    [detail] [nvarchar](500) NULL,
    [created_at] [datetime2](0) NOT NULL,
 CONSTRAINT [PK_review_audit] PRIMARY KEY CLUSTERED
(
    [audit_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
) ON [PRIMARY]
GO

ALTER TABLE [dbo].[review_audit] WITH CHECK ADD CONSTRAINT [FK_review_audit_qc_reviews] FOREIGN KEY([review_id])
REFERENCES [dbo].[qc_reviews] ([review_id])
GO
ALTER TABLE [dbo].[review_audit] CHECK CONSTRAINT [FK_review_audit_qc_reviews]
GO

CREATE NONCLUSTERED INDEX [IX_review_audit_review_id] ON [dbo].[review_audit]
(
    [review_id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, SORT_IN_TEMPDB = OFF, DROP_EXISTING = OFF, ONLINE = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON) ON [PRIMARY]
GO
