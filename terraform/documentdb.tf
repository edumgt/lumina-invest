resource "aws_docdb_subnet_group" "main" {
  name = "lumina-docdb-subnet-group"
  subnet_ids = [
    aws_subnet.private_a.id,
    aws_subnet.private_b.id,
    aws_subnet.private_d.id,
  ]

  tags = { Name = "lumina-docdb-subnet-group" }
}

resource "aws_docdb_cluster" "main" {
  cluster_identifier     = "lumina-docdb"
  engine                 = "docdb"
  master_username        = "docdbuser"
  master_password        = var.docdb_master_password
  db_subnet_group_name   = aws_docdb_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.docdb.id]
  storage_encrypted      = true
  skip_final_snapshot    = false
  final_snapshot_identifier = "lumina-docdb-final"

  tags = { Name = "lumina-docdb" }
}

resource "aws_docdb_cluster_instance" "main" {
  count              = 1
  identifier         = "lumina-docdb-instance-${count.index + 1}"
  cluster_identifier = aws_docdb_cluster.main.id
  instance_class     = "db.t3.medium"

  tags = { Name = "lumina-docdb-instance-${count.index + 1}" }
}
