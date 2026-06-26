data "aws_ami" "amazon_linux_2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_instance" "bastion" {
  ami                    = data.aws_ami.amazon_linux_2023.id
  instance_type          = "t3.medium"
  subnet_id              = aws_subnet.public_c.id
  vpc_security_group_ids = [aws_security_group.ec2_services.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2_ssm.name
  private_ip             = "172.30.2.131"

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
  }

  user_data = base64encode(templatefile("${path.module}/templates/bastion_userdata.sh.tpl", {
    account_id = var.account_id
    region     = var.region
  }))

  tags = { Name = "bastion-host" }
}

resource "aws_eip" "bastion" {
  instance = aws_instance.bastion.id
  domain   = "vpc"
  tags     = { Name = "lumina-bastion-eip" }
}
