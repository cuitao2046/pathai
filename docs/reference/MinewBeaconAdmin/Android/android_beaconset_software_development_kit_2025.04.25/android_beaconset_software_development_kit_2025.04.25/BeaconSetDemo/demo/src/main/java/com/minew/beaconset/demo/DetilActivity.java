package com.minew.beaconset.demo;

import static com.minew.beaconset.MinewBeaconConnection.NULL_PWD;

import android.app.ProgressDialog;
import android.content.DialogInterface;
import android.content.SharedPreferences;
import android.os.Bundle;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AlertDialog;
import androidx.appcompat.app.AppCompatActivity;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;
import androidx.appcompat.widget.Toolbar;

import android.os.Handler;
import android.preference.PreferenceManager;
import android.text.TextUtils;
import android.util.Log;
import android.view.View;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import com.minew.beaconset.ConnectionState;
import com.minew.beaconset.MinewBeaconConnection;
import com.minew.beaconset.MinewBeaconConnectionListener;
import com.minew.beaconset.MinewBeaconSetting;
import com.minew.beaconset.MinewBeaconWriteListener;

import java.util.ArrayList;
import java.util.List;

public class DetilActivity extends AppCompatActivity {

    private RecyclerView mRecycle;
    private DetilListAdapter mAdapter;
    private View mView;
    private EditText mEt_data;

    private TextView mBeacon_save;
    private ProgressDialog mpDialog;
    private MinewBeaconConnection mMinewBeaconConnection;
    private MinewBeaconSetting mMinewBeaconSetting;
    private boolean isPowerOff = false;
    private String mPassword;
    private Handler handler = new Handler();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_detil);

        initView();
        initConnection();
        mMinewBeaconSetting = mMinewBeaconConnection.setting;
        initData();
        initListener();
        dialogshow();
    }

    private void initView() {
        Toolbar toolbar = (Toolbar) findViewById(R.id.detil_toolbar);
        setSupportActionBar(toolbar);

        mRecycle = (RecyclerView) findViewById(R.id.detil_recyeler);
        RecyclerView.LayoutManager layoutManager = new LinearLayoutManager(this);
        mRecycle.setLayoutManager(layoutManager);
        mAdapter = new DetilListAdapter();
        mRecycle.setAdapter(mAdapter);
        mRecycle.addItemDecoration(new RecycleViewDivider(this, LinearLayoutManager.HORIZONTAL));

        mBeacon_save = (TextView) findViewById(R.id.beacon_save);
    }

    private void initConnection() {
        String mac = getIntent().getStringExtra("mac");
        mMinewBeaconConnection = MinewBeaconConnection.minewBeaconConnections.get(mac);
        ConnectionState state = mMinewBeaconConnection.state;
    }

    private void initData() {
        List<String> listname = new ArrayList<>();

        listname.add(getString(R.string.uuid));
        listname.add(getString(R.string.major));
        listname.add(getString(R.string.minor));
        listname.add(getString(R.string.measured_power));
        listname.add(getString(R.string.transimssion_power));
        listname.add(getString(R.string.broadcasting_interval));
        listname.add(getString(R.string.serial_id));
        listname.add(getString(R.string.ibeacon_name));
        listname.add(getString(R.string.connection_mode));
        listname.add(getString(R.string.restart_password));


        //sysinfo
        listname.add(getString(R.string.manufacturer_name));
//        listname.add(getString(R.string.model));
        listname.add(getString(R.string.sys_serial_id));
        listname.add(getString(R.string.hardware_revision));
        listname.add(getString(R.string.firmware_revision));
//        listname.add(getString(R.string.software_revision));
        listname.add(getString(R.string.system_id));
        listname.add(getString(R.string.regulatorycertifactiondata));

        if (mMinewBeaconSetting.isPowerOff()) {
            listname.add(getString(R.string.off));
        }
        mAdapter.setListname(listname);


        List<String> listdata = getList();
        mAdapter.setData(listdata);
    }

    @NonNull
    private List<String> getList() {
        List<String> listdata = new ArrayList<>();
        listdata.add(mMinewBeaconSetting.getUuid());
        listdata.add(mMinewBeaconSetting.getMajor() + "");
        listdata.add(mMinewBeaconSetting.getMinor() + "");
        listdata.add(mMinewBeaconSetting.getCalibratedTxPower() + "");
        listdata.add(mMinewBeaconSetting.getTxPower() + "");
        listdata.add(mMinewBeaconSetting.getBroadcastInterval() + "");
        listdata.add(mMinewBeaconSetting.getDeviceId() + "");
        listdata.add(mMinewBeaconSetting.getName());
        listdata.add(mMinewBeaconSetting.getMode() + "");
        listdata.add(mMinewBeaconSetting.getPassword());


        listdata.add(mMinewBeaconSetting.getManufacture());
//        listdata.add(mMinewBeaconSetting.getModel() + "");
        listdata.add(mMinewBeaconSetting.getSN());
        listdata.add(mMinewBeaconSetting.getHardware());
        listdata.add(mMinewBeaconSetting.getFirmware());
//        listdata.add(mMinewBeaconSetting.getSoftware());
        listdata.add(mMinewBeaconSetting.getSystemId());
        listdata.add(mMinewBeaconSetting.getCertData());
        if (mMinewBeaconSetting.isPowerOff()) {
            listdata.add("");
        }
        return listdata;
    }

    private void initListener() {
        mAdapter.setOnItemClickLitener(new DetilListAdapter.OnItemClickLitener() {
            @Override
            public void onItemClick(View view, final int position) {
                isPowerOff = false;
                switch (position) {
                    case 0:
                    case 1:
                    case 2:
                    case 3:
                    case 4:
                    case 5:
                    case 6:
                    case 7:
                    case 8:
                    case 9:
                        excute(position);
                        break;
                    case 16:
                        powerOff();
                        break;

                }
            }

            @Override
            public void onItemLongClick(View view, int position) {

            }
        });

        mBeacon_save.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                mView = View.inflate(DetilActivity.this, R.layout.dialog_edittext, null);
                mEt_data = (EditText) mView.findViewById(R.id.et_data);
                AlertDialog.Builder builder = new AlertDialog.Builder(DetilActivity.this);
                builder.setTitle("Restart Beacon").setMessage("Please input restart password");
                builder.setView(mView);
                builder.setNegativeButton("cancel", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        dialog.dismiss();
                    }
                }).setPositiveButton("ok", new DialogInterface.OnClickListener() {
                    @Override
                    public void onClick(DialogInterface dialog, int which) {
                        dialog.dismiss();
                        String password = mEt_data.getText().toString().trim();
                        if (password.length() <= 0) {
                            Toast.makeText(getApplicationContext(), "password can not null", Toast.LENGTH_SHORT).show();
                            return;
                        }
                        mpDialog.setMessage(getString(R.string.setting));
                        mpDialog.show();
                        if (password.length() > 0 && password.length() == 8) {
                            // Modify/Add password,it will add password if device has no password, update password if device has a password
                            // (Warning/Note)!!!: the password string length must be 8, digits or letters,Otherwise, it may cause the device firmware to malfunction
                            mMinewBeaconConnection.writeSetting(mEt_data.getText().toString().trim());
                        }
                    }
                });
                AlertDialog dialog = builder.create();
                dialog.show();
            }
        });

        mMinewBeaconConnection.setMinewBeaconConnectionListener(new MinewBeaconConnectionListener() {
            @Override
            public void onChangeState(MinewBeaconConnection connection, ConnectionState state) {
                switch (state) {
                    case BeaconStatus_Disconnect:
                    case BeaconStatus_ConnectFailed:
                        runOnUiThread(new Runnable() {
                            @Override
                            public void run() {
                                finish();
                            }
                        });
                        break;
                }
            }

            @Override
            public void onWriteSettings(MinewBeaconConnection minewBeaconConnection, final boolean success, boolean b1) {
                mpDialog.dismiss();
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        if (success) {
                            Toast.makeText(getApplicationContext(), "Success!", Toast.LENGTH_SHORT).show();
                        } else {
                            Toast.makeText(getApplicationContext(), "Fail!", Toast.LENGTH_SHORT).show();
                        }
                        finish();

                    }
                });
            }

//            @Override
//            public void onWriteSettings(MinewBeaconConnection connection, final boolean success) {
//
//            }
        });
    }

    protected void powerOff() {
        if (!mMinewBeaconSetting.isPowerOff()) {
            return;
        }
        if(mMinewBeaconConnection!=null){
            mMinewBeaconConnection.setMinewBeaconWriteListener(new MinewBeaconWriteListener() {
                @Override
                public void onPowerOff(final boolean success) {
                    try {
                        runOnUiThread(new Runnable() {
                            @Override
                            public void run() {
                                if (success) {
                                    Toast.makeText(getApplicationContext(), getResources().getString(R.string.powoff_success), Toast.LENGTH_SHORT).show();
                                    Log.e("tag", "DetilActivity " + "onPowerOff success");
                                }else {
                                    if (mpDialog!=null&&mpDialog.isShowing()){
                                        mpDialog.dismiss();
                                    }
                                    Toast.makeText(getApplicationContext(), getResources().getString(R.string.powoff_fail), Toast.LENGTH_SHORT).show();
                                    Log.e("tag", "DetilActivity " + "onPowerOff fail");
                                }
                            }
                        });
                    }catch (Exception e){
                        Log.e("tag", e.getMessage());
                    }
                }
            });
        }
        try {

            AlertDialog.Builder builder = new AlertDialog.Builder(DetilActivity.this);
            builder.setTitle("Beacon Set");
            builder.setMessage(R.string.powerofftip);
            builder.setNegativeButton("cancel", new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    dialog.dismiss();
                }
            }).setPositiveButton("ok", new DialogInterface.OnClickListener() {
                @Override
                public void onClick(DialogInterface dialog, int which) {
                    dialog.dismiss();
//                mMinewBeaconConnection.writePowerOffByAuth();

                    boolean needPWD = false;
                    String firmWare = mMinewBeaconSetting.getFirmware();
                    if (TextUtils.isEmpty(firmWare)){
                        needPWD = true;
                    }else {
                        try {
                            firmWare = firmWare.replace(".","");
                            int fw = Integer.parseInt(firmWare);
                            if (fw>1005){
                                //大于固件版本 1.0.05
                                needPWD = true;
                            }
                        }catch (Exception e){
                            needPWD = true;
                            Log.e("minew",e.getMessage());
                        }
                    }
                    if (needPWD){

                        mView = View.inflate(DetilActivity.this, R.layout.dialog_edittext, null);
                        android.app.AlertDialog.Builder builderO = new android.app.AlertDialog.Builder(DetilActivity.this);
                        builderO.setMessage(getResources().getString(R.string.input_password));
                        builderO.setView(mView);
                        mEt_data = (EditText) mView.findViewById(R.id.et_data);
                        SharedPreferences mPreferences;
                        mPreferences = PreferenceManager.getDefaultSharedPreferences(DetilActivity.this);
                        String resortpwd = mPreferences.getString("resortpwd", "");
                        resortpwd="";
                        mEt_data.setText(resortpwd);
                        mEt_data.setSelection(resortpwd.length());
                        builderO.setNegativeButton(getResources().getString(R.string.cancel), new DialogInterface.OnClickListener() {
                                    @Override
                                    public void onClick(DialogInterface dialog, int which) {
                                        dialog.dismiss();
                                    }
                                })
                                .setPositiveButton(getResources().getString(R.string.ok), new DialogInterface.OnClickListener() {
                                    @Override
                                    public void onClick(DialogInterface dialog, int which) {
                                        dialog.dismiss();
                                        dialogshow();
                                        String password = mEt_data.getText().toString().trim();
                                        if (TextUtils.isEmpty(password)) {
                                            Toast.makeText(getApplicationContext(), getResources().getString(R.string.password_notnull), Toast.LENGTH_SHORT).show();
                                            return;
                                        }
                                        if (password.length() != 8) {
                                            Toast.makeText(getApplicationContext(), getResources().getString(R.string.pwd_length_8), Toast.LENGTH_SHORT).show();
                                            return;
                                        }
                                        mMinewBeaconConnection.writePowerOffByAuth(password);
                                        isPowerOff = true;
                                        mPassword = password;
                                    }
                                });
                        android.app.AlertDialog dialogAuth = builderO.create();
                        dialogAuth.show();

                    }else {
                        mMinewBeaconConnection.writePowerOffByAuth(NULL_PWD);
                    }
                }
            });
            AlertDialog dialog = builder.create();
            dialog.show();

        }catch (Exception e){
            Log.e("tag",e.getMessage());
        }

    }

    protected void excute(final int position) {
        mView = View.inflate(DetilActivity.this, R.layout.dialog_edittext, null);
        mEt_data = (EditText) mView.findViewById(R.id.et_data);
        String data = mAdapter.getData(position);
        mEt_data.setText(data);
        AlertDialog.Builder builder = new AlertDialog.Builder(DetilActivity.this);
        builder.setTitle("Beacon Set");
        builder.setView(mView);
        builder.setNegativeButton("cancel", new DialogInterface.OnClickListener() {
            @Override
            public void onClick(DialogInterface dialog, int which) {
                dialog.dismiss();
            }
        }).setPositiveButton("ok", new DialogInterface.OnClickListener() {
            @Override
            public void onClick(DialogInterface dialog, int which) {
                dialog.dismiss();
                String data = mEt_data.getText().toString().trim();
                try {
                    switch (position) {
                        case 0:
                            if (!mMinewBeaconSetting.getUuid().equals(data)) {
                                mMinewBeaconSetting.setUuid(data);
                            }
                            break;
                        case 1:
                            if (mMinewBeaconSetting.getMajor() != Integer.parseInt(data)) {
                                mMinewBeaconSetting.setMajor(Integer.parseInt(data));
                            }
                            break;
                        case 2:
                            if (mMinewBeaconSetting.getMinor() != Integer.parseInt(data)) {
                                mMinewBeaconSetting.setMinor(Integer.parseInt(data));
                            }
                            break;
                        case 3:
                            if (mMinewBeaconSetting.getCalibratedTxPower() != Integer.parseInt(data)) {
                                mMinewBeaconSetting.setCalibratedTxPower(Integer.parseInt(data));
                            }
                            break;
                        case 4:
                            if (mMinewBeaconSetting.getTxPower() != Integer.parseInt(data)) {
                                mMinewBeaconSetting.setTxPower(Integer.parseInt(data));
                            }
                            break;
                        case 5:
                            if (mMinewBeaconSetting.getBroadcastInterval() != Integer.parseInt(data)) {
                                mMinewBeaconSetting.setBroadcastInterval(Integer.parseInt(data));
                            }
                            break;
                        case 6:
                            if (!mMinewBeaconSetting.getDeviceId().equals(data)) {
                                mMinewBeaconSetting.setDeviceId(data);
                            }
                            break;
                        case 7:
                            if (!mMinewBeaconSetting.getName().equals(data)) {
                                mMinewBeaconSetting.setName(data);
                            }
                            break;
                        case 8:
                            if (mMinewBeaconSetting.getMode() != Integer.parseInt(data)) {
                                mMinewBeaconSetting.setMode(Integer.parseInt(data));
                            }
                            break;
                        case 9:
                            if (data.length() > 0 && data.length() == 8) {
                                // Modify/Add password,it will add password if device has no password, update password if device has a password
                                // (Warning/Note)!!!: the password string length must be 8, digits or letters,Otherwise, it may cause the device firmware to malfunction
                                mMinewBeaconSetting.setPassword(data);
                            }
                            break;

                    }
                } catch (NumberFormatException e) {
                    e.printStackTrace();
                }
                List<String> list = getList();
                mAdapter.setData(list);
            }
        });
        AlertDialog dialog = builder.create();
        dialog.show();
    }

    protected void dialogshow() {
        mpDialog = new ProgressDialog(DetilActivity.this);
        mpDialog.setProgressStyle(ProgressDialog.STYLE_SPINNER);
        mpDialog.setTitle(null);//
        mpDialog.setIcon(null);//
        mpDialog.setOnCancelListener(new DialogInterface.OnCancelListener() {
            @Override
            public void onCancel(DialogInterface arg0) {

            }
        });
        mpDialog.setCancelable(true);//
        mpDialog.setCanceledOnTouchOutside(false);
    }

    @Override
    protected void onDestroy() {
        if (mpDialog != null && mpDialog.isShowing()) {
            mpDialog.dismiss();
        }
        mMinewBeaconConnection.disconnect();
        super.onDestroy();
    }

    @Override
    public void finish() {
        if (isPowerOff&&!TextUtils.isEmpty(mPassword)){
            handler.post(new Runnable() {
                @Override
                public void run() {
                    if (mpDialog!=null&&mpDialog.isShowing()){
                        mpDialog.dismiss();
                    }
                    Toast.makeText(getApplicationContext(), getResources().getString(R.string.powoff_success), Toast.LENGTH_SHORT).show();
                    Log.v("tag","DetilActivity " + "pf");
                    SharedPreferences mPreferences = PreferenceManager.getDefaultSharedPreferences(DetilActivity.this);
                    SharedPreferences.Editor edit = mPreferences.edit();
                    edit.putString("resortpwd", mPassword);
                    edit.commit();
                }
            });
        }
        handler.postDelayed(new Runnable() {
            @Override
            public void run() {
                DetilActivity.super.finish();
            }
        },2000);
    }

    private String formatUUID(String str) {
        if (str.length() < 32) {
            return str;
        }
        return str.substring(0, 8) + '-' + str.substring(8, 12) + '-' + str.substring(12, 16) + '-' + str.substring(16, 20) + '-' + str.substring(20, 32);
    }

}
