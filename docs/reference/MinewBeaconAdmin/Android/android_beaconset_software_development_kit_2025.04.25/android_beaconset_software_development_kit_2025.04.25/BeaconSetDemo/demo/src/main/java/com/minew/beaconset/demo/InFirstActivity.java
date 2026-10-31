package com.minew.beaconset.demo;

import android.content.Intent;
import android.os.Bundle;
import androidx.appcompat.app.AppCompatActivity;
import android.view.View;



public class InFirstActivity extends AppCompatActivity {


    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_in_first);
    }



    public void toMain(View view) {
        startActivity(new Intent(this, MainActivity.class));
    }



}
