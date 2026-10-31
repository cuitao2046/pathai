using BeaconProvisioner.Protocol;

namespace BeaconProvisioner.Tests;

public class BeaconCodecTests
{
    [Fact]
    public void Uuid_converts_to_16_bytes_and_round_trips()
    {
        const string uuid = "B9407F30-F5F8-466E-AFF9-25556B57FE6D";
        var bytes = BeaconCodec.UuidToBytes(uuid);

        Assert.Equal(16, bytes.Length);
        Assert.Equal(uuid, BeaconCodec.UuidFromBytes(bytes));
    }

    [Fact]
    public void Uuid_accepts_lowercase_without_dashes()
    {
        var bytes = BeaconCodec.UuidToBytes("b9407f30f5f8466eaff925556b57fe6d");
        Assert.Equal("B9407F30-F5F8-466E-AFF9-25556B57FE6D", BeaconCodec.UuidFromBytes(bytes));
    }

    [Fact]
    public void Uuid_rejects_wrong_length()
    {
        Assert.Throws<ArgumentException>(() => BeaconCodec.UuidToBytes("B9407F30-F5F8-466E-AFF9"));
    }

    [Fact]
    public void Interval_encodes_big_endian()
    {
        Assert.Equal(new byte[] { 0x01, 0x2C }, BeaconCodec.UInt16BigEndian(300));
        Assert.Equal(new byte[] { 0x27, 0x10 }, BeaconCodec.UInt16BigEndian(10_000));
    }

    [Theory]
    [InlineData(99, false)]
    [InlineData(100, true)]
    [InlineData(300, true)]
    [InlineData(10_000, true)]
    [InlineData(10_001, false)]
    public void Interval_range_follows_manual(int ms, bool valid)
    {
        Assert.Equal(valid, BeaconCodec.IsValidInterval(ms));
    }

    [Fact]
    public void Power_code_maps_dbm()
    {
        Assert.Equal(3, BeaconCodec.PowerCodeFor(-12));
        Assert.Equal(-12.0, BeaconCodec.PowerDbmFor(3));
        Assert.Equal(-1, BeaconCodec.PowerCodeFor(-15));
    }
}
