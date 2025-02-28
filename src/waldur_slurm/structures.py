import collections

Account = collections.namedtuple('Account', ['name', 'description', 'organization'])
Association = collections.namedtuple('Association', ['account', 'user', 'value'])


class Quotas:
    def __init__(self, cpu=0, gpu=0, ram=0):
        self.cpu = cpu
        self.gpu = gpu
        self.ram = ram

    def __add__(self, other):
        return Quotas(
            self.cpu + other.cpu,
            self.gpu + other.gpu,
            self.ram + other.ram,
        )

    def __str__(self):
        return "Quotas: CPU={}, GPU={}, RAM={}".format(
            self.cpu,
            self.gpu,
            self.ram,
        )

    def __repr__(self) -> str:
        return self.__str__()
